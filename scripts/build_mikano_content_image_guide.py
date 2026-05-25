import html
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


OUTPUT_DIR = Path(os.environ.get("MIKANO_OUTPUT_DIR", "public"))
INPUT_JSON = OUTPUT_DIR / "mikano-image-crawl.json"
OUTPUT_HTML = OUTPUT_DIR / "index.html"
OUTPUT_JSON = OUTPUT_DIR / "mikano-content-image-guide-data.json"
WORKFLOW_URL = os.environ.get(
    "MIKANO_WORKFLOW_URL",
    "https://github.com/Prod-Sam103/mm-image-guide/actions/workflows/refresh-image-guide.yml",
)
PAGES_URL = os.environ.get("MIKANO_PAGES_URL", "https://prod-sam103.github.io/mm-image-guide/")


PAGE_ORDER = [
    "home",
    "brand",
    "brand listing",
    "vehicle",
    "vehicle listing",
    "blog",
    "blog listing",
    "news/event",
    "news/event listing",
    "static page",
    "promo/listing",
    "other",
]

PAGE_LABELS = {
    "home": "Home",
    "brand": "Brands",
    "brand listing": "Brand Listings",
    "vehicle": "Vehicles",
    "vehicle listing": "Vehicle Listings",
    "blog": "Blog",
    "blog listing": "Blog Listings",
    "news/event": "News / Events",
    "news/event listing": "News / Event Listings",
    "static page": "Static Pages",
    "promo/listing": "Promos / Listings",
    "other": "Other",
}

EXCLUDED_FILE_PATTERNS = (
    "logo",
    "favicon",
    "mikano_valueproposition_icons",
    "icons_",
)


def esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def safe_slug(value):
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")


def rounded_up(value, step=10):
    if not value:
        return 0
    return int(math.ceil(value / step) * step)


def gcd(width, height):
    width = int(width or 0)
    height = int(height or 0)
    if width <= 0 or height <= 0:
        return 1
    while height:
        width, height = height, width % height
    return width or 1


def ratio_text(width, height):
    width = int(width or 0)
    height = int(height or 0)
    if not width or not height:
        return ""
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def decimal_ratio(width, height):
    if not width or not height:
        return 0
    return round(width / height, 4)


def display_size(row, prefix):
    width = int(row.get(f"{prefix}Width") or 0)
    height = int(row.get(f"{prefix}Height") or 0)
    return width, height


def is_hosted_content_image(row):
    src = row.get("sourceIdentity") or row.get("sourceUrl") or ""
    file_name = (row.get("fileName") or "").lower()
    if not src or src.startswith("data:"):
        return False
    if "mm-api.yokeserver.com/media/" not in src:
        return False
    if any(pattern in file_name for pattern in EXCLUDED_FILE_PATTERNS):
        return False
    if row.get("visibilityStatus") != "visible":
        return False

    desktop_w, desktop_h = display_size(row, "desktop")
    mobile_w, mobile_h = display_size(row, "mobile")
    largest_w = max(desktop_w, mobile_w)
    largest_h = max(desktop_h, mobile_h)
    original_w = int(row.get("originalWidth") or 0)
    original_h = int(row.get("originalHeight") or 0)

    major_slot = desktop_w >= 600 and desktop_h >= 180
    usable_content_slot = largest_w >= 120 and largest_h >= 90
    strong_source = original_w >= 450 and original_h >= 180 and largest_w >= 100 and largest_h >= 60

    return major_slot or usable_content_slot or strong_source


def page_title(url):
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return "Homepage"
    label = path.split("/")[-1].replace("-", " ").replace("_", " ")
    return " ".join(word.capitalize() for word in label.split())[:90]


def slot_kind(row):
    desktop_w, desktop_h = display_size(row, "desktop")
    ratio = decimal_ratio(desktop_w, desktop_h)
    if desktop_w >= 900 and desktop_h >= 260:
        return "Hero / banner"
    if desktop_h >= 500 and ratio < 1:
        return "Portrait feature"
    if 1.45 <= ratio <= 1.95 and desktop_w >= 250:
        return "Card / listing image"
    if 2.0 <= ratio <= 2.5 and desktop_w >= 250:
        return "Wide card / banner"
    if 0.85 <= ratio <= 1.2:
        return "Square / product image"
    return "Content image"


def ideal_guidance(row):
    desktop_w, desktop_h = display_size(row, "desktop")
    mobile_w, mobile_h = display_size(row, "mobile")
    original_w = int(row.get("originalWidth") or 0)
    original_h = int(row.get("originalHeight") or 0)

    min_w = rounded_up(desktop_w)
    min_h = rounded_up(desktop_h)
    sharp_w = rounded_up(desktop_w * 2)
    sharp_h = rounded_up(desktop_h * 2)
    desktop_ratio = row.get("desktopRatio") or ratio_text(desktop_w, desktop_h)

    notes = []
    if desktop_w and desktop_h:
        notes.append(f"Design at {desktop_ratio}. Minimum export: {min_w} x {min_h}px.")
        notes.append(f"For sharper uploads, use around {sharp_w} x {sharp_h}px.")
    if mobile_w and mobile_h:
        notes.append(f"Mobile renders near {mobile_w} x {mobile_h}px.")
    if original_w and desktop_w and (original_w < desktop_w or original_h < desktop_h):
        notes.append("Current asset is smaller than the desktop display size.")

    asset_ratio = decimal_ratio(original_w, original_h)
    display_ratio = decimal_ratio(desktop_w, desktop_h)
    if asset_ratio and display_ratio:
        delta = abs(asset_ratio - display_ratio) / display_ratio
        if delta > 0.18:
            notes.append("Asset ratio differs from the display slot; future artwork should match the slot ratio.")

    return " ".join(notes)


def design_brief(row):
    desktop_w, desktop_h = display_size(row, "desktop")
    mobile_w, mobile_h = display_size(row, "mobile")
    if not desktop_w or not desktop_h:
        return {
            "designSize": "",
            "minimumSize": "",
            "designRatio": "",
            "plainInstruction": "Use the current image proportions as the design reference.",
            "mobileInstruction": "",
        }

    design_w = rounded_up(desktop_w * 2)
    design_h = rounded_up(desktop_h * 2)
    min_w = rounded_up(desktop_w)
    min_h = rounded_up(desktop_h)
    ratio = row.get("desktopRatio") or ratio_text(desktop_w, desktop_h)
    mobile_note = f"Mobile: {mobile_w} x {mobile_h}px" if mobile_w and mobile_h else ""

    return {
        "designSize": f"{design_w} x {design_h}px",
        "minimumSize": f"{min_w} x {min_h}px",
        "designRatio": ratio,
        "plainInstruction": f"Design this artwork at {design_w} x {design_h}px ({ratio}).",
        "mobileInstruction": mobile_note,
    }


def normalize_rows(data):
    candidates = [row for row in data["imageRows"] if is_hosted_content_image(row)]
    grouped = {}
    for row in candidates:
        key = (row["pageUrl"], row["sourceIdentity"])
        desktop_w, desktop_h = display_size(row, "desktop")
        area = desktop_w * desktop_h
        current = grouped.get(key)
        if current is None or area > current["_area"]:
            grouped[key] = {**row, "_area": area}

    rows = []
    for idx, row in enumerate(grouped.values(), start=1):
        desktop_w, desktop_h = display_size(row, "desktop")
        mobile_w, mobile_h = display_size(row, "mobile")
        original_w = int(row.get("originalWidth") or 0)
        original_h = int(row.get("originalHeight") or 0)
        row = {k: v for k, v in row.items() if k != "_area"}
        row["id"] = f"img-{idx}"
        row["pageTitle"] = page_title(row["pageUrl"])
        row["pageLabel"] = PAGE_LABELS.get(row["pageType"], row["pageType"])
        row["slotKind"] = slot_kind(row)
        row["desktopSize"] = f"{desktop_w} x {desktop_h}px" if desktop_w and desktop_h else ""
        row["mobileSize"] = f"{mobile_w} x {mobile_h}px" if mobile_w and mobile_h else ""
        row["originalSize"] = f"{original_w} x {original_h}px" if original_w and original_h else ""
        row["idealGuidance"] = ideal_guidance(row)
        row.update(design_brief(row))
        row["ratioMismatch"] = False
        row["undersized"] = False
        if original_w and original_h and desktop_w and desktop_h:
            row["undersized"] = original_w < desktop_w or original_h < desktop_h
            display_ratio = desktop_w / desktop_h
            asset_ratio = original_w / original_h
            row["ratioMismatch"] = abs(asset_ratio - display_ratio) / display_ratio > 0.18
        row["missingAlt"] = not bool((row.get("altText") or "").strip())
        row["watchlist"] = row["undersized"] or row["ratioMismatch"] or row["missingAlt"]
        rows.append(row)

    page_rank = {name: index for index, name in enumerate(PAGE_ORDER)}
    rows.sort(key=lambda row: (page_rank.get(row["pageType"], 99), row["pageUrl"], row["imageOrder"], row["fileName"]))
    return rows


def recommended_sizes(rows):
    groups = defaultdict(list)
    for row in rows:
        desktop_w, desktop_h = display_size(row, "desktop")
        if desktop_w and desktop_h:
            groups[row.get("desktopRatio") or ratio_text(desktop_w, desktop_h)].append(row)

    recs = []
    for ratio, group in groups.items():
        max_w = max(int(row.get("desktopWidth") or 0) for row in group)
        max_h = max(int(row.get("desktopHeight") or 0) for row in group)
        recs.append(
            {
                "ratio": ratio,
                "count": len(group),
                "slotKind": Counter(row["slotKind"] for row in group).most_common(1)[0][0],
                "minimum": f"{rounded_up(max_w)} x {rounded_up(max_h)}px",
                "recommended": f"{rounded_up(max_w * 2)} x {rounded_up(max_h * 2)}px",
                "instruction": f"Design at {rounded_up(max_w * 2)} x {rounded_up(max_h * 2)}px",
                "examplePage": group[0]["pageUrl"],
            }
        )
    recs.sort(key=lambda item: (-item["count"], item["ratio"]))
    return recs[:18]


def page_groups(rows):
    grouped = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row["pageType"]][row["pageUrl"]].append(row)
    return grouped


def issue_rows(rows):
    issues = []
    for row in rows:
        labels = []
        if row["undersized"]:
            labels.append("Undersized")
        if row["ratioMismatch"]:
            labels.append("Ratio mismatch")
        if row["missingAlt"]:
            labels.append("Missing alt text")
        if labels:
            issues.append({**row, "issueLabels": labels})
    return issues


def write_filtered_json(data, rows, recs, issues):
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceCrawlGeneratedAt": data.get("generatedAt"),
        "sitemapUrl": data.get("sitemapUrl"),
        "pagesCrawled": len(data.get("pageResults", [])),
        "contentImageCount": len(rows),
        "uniqueContentAssets": len({row["sourceIdentity"] for row in rows}),
        "contentRows": rows,
        "recommendedSizes": recs,
        "issues": issues,
        "pagesWithNoContentImages": pages_with_no_content_images(data, rows),
    }
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2))
    return payload


def pages_with_no_content_images(data, rows):
    pages_with_rows = {row["pageUrl"] for row in rows}
    return [
        {"pageUrl": page["url"], "pageType": page["pageType"], "pageLabel": PAGE_LABELS.get(page["pageType"], page["pageType"])}
        for page in data.get("pageResults", [])
        if page["url"] not in pages_with_rows
    ]


def render_summary_cards(payload):
    ratio = Counter(row.get("desktopRatio") or "Unknown" for row in payload["contentRows"]).most_common(1)
    most_common_ratio = ratio[0][0] if ratio else "n/a"
    cards = [
        ("Pages crawled", payload["pagesCrawled"]),
        ("Content images", payload["contentImageCount"]),
        ("Unique assets", payload["uniqueContentAssets"]),
        ("Top desktop ratio", most_common_ratio),
        ("No content images", len(payload["pagesWithNoContentImages"])),
    ]
    return "\n".join(
        f"""
        <article class="metric-card">
          <span>{esc(label)}</span>
          <strong>{esc(value)}</strong>
        </article>
        """
        for label, value in cards
    )


def render_sidebar(rows):
    counts = Counter(row["pageType"] for row in rows)
    items = []
    for page_type in PAGE_ORDER:
        if counts[page_type]:
            label = PAGE_LABELS.get(page_type, page_type)
            items.append(
                f'<a href="#section-{safe_slug(page_type)}"><span>{esc(label)}</span><strong>{counts[page_type]}</strong></a>'
            )
    return "\n".join(items)


def render_card(row):
    issue_badges = []
    if row["undersized"]:
        issue_badges.append('<span class="badge warn">Undersized</span>')
    if row["ratioMismatch"]:
        issue_badges.append('<span class="badge warn">Ratio mismatch</span>')
    if row["missingAlt"]:
        issue_badges.append('<span class="badge quiet">Missing alt</span>')
    badges = "\n".join(issue_badges) or '<span class="badge ok">Looks usable</span>'
    source = row["sourceIdentity"]
    page = row["pageUrl"]
    return f"""
    <article class="image-card" data-search="{esc(' '.join([row['pageUrl'], row['fileName'], row.get('altText',''), row['slotKind'], row['pageLabel'], row.get('desktopRatio','')]).lower())}" data-page-type="{esc(row['pageType'])}" data-issue="{str(row['watchlist']).lower()}">
      <a class="thumb" href="{esc(source)}" target="_blank" rel="noreferrer">
        <img src="{esc(source)}" alt="{esc(row.get('altText') or row['fileName'])}" loading="lazy">
      </a>
      <div class="card-body">
        <div class="card-topline">
          <span class="slot">{esc(row['slotKind'])}</span>
          <span class="ratio">{esc(row.get('designRatio') or '')}</span>
        </div>
        <h3>{esc(row['fileName'])}</h3>
        <p class="alt">{esc(row.get('altText') or 'No alt text supplied')}</p>
        <div class="design-callout">
          <span>Design this</span>
          <strong>{esc(row['designSize'])}</strong>
          <em>{esc(row.get('designRatio') or '')}</em>
        </div>
        <div class="detail-strip">
          <span>Minimum {esc(row['minimumSize'])}</span>
          <span>Current file {esc(row['originalSize'])}</span>
          <span>{esc(row['mobileInstruction'])}</span>
        </div>
        <div class="links">
          <a href="{esc(page)}" target="_blank" rel="noreferrer">Open page</a>
          <a href="{esc(source)}" target="_blank" rel="noreferrer">Open image</a>
        </div>
        <div class="badges">{badges}</div>
      </div>
    </article>
    """


def render_design_sections(rows):
    grouped = page_groups(rows)
    sections = []
    for page_type in PAGE_ORDER:
        pages = grouped.get(page_type)
        if not pages:
            continue
        page_blocks = []
        for page_url, page_rows in pages.items():
            cards = "\n".join(render_card(row) for row in page_rows)
            page_blocks.append(
                f"""
                <section class="page-block">
                  <div class="page-heading">
                    <div>
                      <span>{esc(PAGE_LABELS.get(page_type, page_type))}</span>
                      <h3>{esc(page_title(page_url))}</h3>
                    </div>
                    <a href="{esc(page_url)}" target="_blank" rel="noreferrer">{esc(page_url)}</a>
                  </div>
                  <div class="card-grid">{cards}</div>
                </section>
                """
            )
        sections.append(
            f"""
            <section class="type-section" id="section-{safe_slug(page_type)}">
              <h2>{esc(PAGE_LABELS.get(page_type, page_type))}</h2>
              {''.join(page_blocks)}
            </section>
            """
        )
    return "\n".join(sections)


def render_recommended_sizes(recs):
    rows = "\n".join(
        f"""
        <tr>
          <td><strong>{esc(rec['slotKind'])}</strong></td>
          <td><span class="recipe-size">{esc(rec['instruction'])}</span></td>
          <td>{esc(rec['ratio'])}</td>
          <td>{esc(rec['minimum'])}</td>
          <td>{esc(rec['count'])} places</td>
          <td><a href="{esc(rec['examplePage'])}" target="_blank" rel="noreferrer">Example page</a></td>
        </tr>
        """
        for rec in recs
    )
    return f"""
    <section class="panel" id="recommended-sizes">
      <div class="section-title">
        <span>Give this to the designer</span>
        <h2>Design Recipes</h2>
      </div>
      <p class="section-copy">Use the “Design at” size as the artwork canvas. The minimum size is the smallest acceptable export, but the design size gives sharper results on modern screens.</p>
      <table>
        <thead><tr><th>Image Use</th><th>Design At</th><th>Ratio</th><th>Minimum</th><th>Used In</th><th>Example</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    """


def render_watchlist(payload):
    issue_items = payload["issues"][:80]
    no_content = payload["pagesWithNoContentImages"]
    issue_cards = "\n".join(
        f"""
        <li>
          <strong>{esc(', '.join(row['issueLabels']))}</strong>
          <a href="{esc(row['pageUrl'])}" target="_blank" rel="noreferrer">{esc(row['pageTitle'])}</a>
          <span>{esc(row['fileName'])}</span>
        </li>
        """
        for row in issue_items
    )
    empty_pages = "\n".join(
        f'<li><a href="{esc(page["pageUrl"])}" target="_blank" rel="noreferrer">{esc(page["pageUrl"])}</a></li>'
        for page in no_content
    )
    return f"""
    <section class="watchlist panel" id="watchlist">
      <div class="section-title">
        <span>Quality checks</span>
        <h2>Issues / Watchlist</h2>
      </div>
      <div class="watch-grid">
        <div>
          <h3>Images to review</h3>
          <ul>{issue_cards or '<li>No major image sizing issues detected.</li>'}</ul>
        </div>
        <div>
          <h3>Pages with no content images</h3>
          <ul>{empty_pages or '<li>All pages have content images.</li>'}</ul>
        </div>
      </div>
    </section>
    """


def render_html(payload):
    rows = payload["contentRows"]
    generated = datetime.now().strftime("%d %b %Y, %H:%M")
    page_type_options = "\n".join(
        f'<option value="{esc(page_type)}">{esc(PAGE_LABELS.get(page_type, page_type))}</option>'
        for page_type in PAGE_ORDER
        if any(row["pageType"] == page_type for row in rows)
    )
    data_json = json.dumps(
        {
            "contentImageCount": payload["contentImageCount"],
            "generatedAt": payload["generatedAt"],
        }
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mikano Motors Content Image Design Guide</title>
  <style>
    :root {{
      --background: #f8fafc;
      --foreground: #0f172a;
      --muted: #64748b;
      --muted-foreground: #475569;
      --border: #e2e8f0;
      --card: #ffffff;
      --primary: #0f172a;
      --accent: #0f766e;
      --accent-soft: #ccfbf1;
      --warning: #b45309;
      --warning-soft: #fef3c7;
      --success: #15803d;
      --success-soft: #dcfce7;
      --shadow: 0 1px 2px rgba(15, 23, 42, .06), 0 12px 32px rgba(15, 23, 42, .08);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--foreground);
      background:
        linear-gradient(180deg, rgba(15, 118, 110, .08), rgba(248, 250, 252, 0) 360px),
        var(--background);
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .layout {{ display: grid; grid-template-columns: 280px minmax(0, 1fr); min-height: 100vh; }}
    aside {{
      position: sticky;
      top: 0;
      height: 100vh;
      padding: 28px 20px;
      background: #020617;
      color: #fff;
      overflow: auto;
    }}
    .brand-mark {{ display: flex; gap: 10px; align-items: center; margin-bottom: 28px; }}
    .mark {{ width: 42px; height: 42px; border-radius: 8px; background: linear-gradient(135deg, #14b8a6, #f8fafc); }}
    .brand-mark strong {{ display: block; font-size: 15px; }}
    .brand-mark span {{ display: block; color: #94a3b8; font-size: 12px; }}
    nav a {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: #e2e8f0;
      padding: 10px 12px;
      border-radius: 8px;
      margin-bottom: 4px;
    }}
    nav a:hover {{ background: rgba(255,255,255,.08); text-decoration: none; }}
    nav strong {{ color: #5eead4; font-size: 12px; }}
    .side-links {{ margin-top: 26px; padding-top: 20px; border-top: 1px solid rgba(255,255,255,.16); }}
    .side-links a {{ color: #fff; display: block; margin-bottom: 10px; font-size: 13px; }}
    main {{ padding: 34px; max-width: 1680px; min-width: 0; }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.3fr) minmax(360px, .7fr);
      gap: 22px;
      align-items: stretch;
      margin-bottom: 24px;
    }}
    .intro, .controls, .panel, .page-block {{
      background: rgba(255,255,255,.94);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-width: 0;
    }}
    .intro {{ padding: 28px; overflow: hidden; position: relative; }}
    .intro:after {{
      content: "";
      position: absolute;
      right: -90px;
      top: -110px;
      width: 290px;
      height: 290px;
      background: radial-gradient(circle, rgba(20,184,166,.22), rgba(20,184,166,0) 66%);
      pointer-events: none;
    }}
    .eyebrow {{ color: var(--accent); font-weight: 800; text-transform: uppercase; font-size: 12px; letter-spacing: .08em; }}
    h1 {{ font-size: 34px; line-height: 1.08; margin: 10px 0 12px; letter-spacing: 0; }}
    .intro p {{ color: var(--muted-foreground); font-size: 15px; line-height: 1.6; max-width: 780px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap: 12px; margin-top: 22px; }}
    .metric-card {{ background: #f8fafc; border: 1px solid var(--border); padding: 14px; border-radius: 8px; }}
    .metric-card span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric-card strong {{ display: block; font-size: 25px; margin-top: 6px; }}
    .controls {{ padding: 20px; display: grid; gap: 12px; align-content: start; }}
    .controls label {{ font-size: 12px; font-weight: 700; color: var(--muted); }}
    input, select {{
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 11px 12px;
      font: inherit;
      background: #fff;
      color: var(--foreground);
    }}
    .check-row {{ display: flex; gap: 8px; align-items: center; font-size: 13px; color: var(--muted); }}
    .check-row input {{ width: auto; }}
    .panel {{ padding: 24px; margin-bottom: 24px; overflow: auto; }}
    .section-copy {{ color: var(--muted-foreground); max-width: 780px; line-height: 1.55; margin-top: -8px; }}
    .section-title span, .page-heading span {{ color: var(--muted); font-weight: 700; font-size: 12px; text-transform: uppercase; }}
    h2 {{ margin: 6px 0 18px; font-size: 24px; letter-spacing: 0; }}
    table {{ border-collapse: collapse; width: 100%; min-width: 820px; }}
    th, td {{ border-bottom: 1px solid var(--border); padding: 12px 10px; text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; background: #f7fafc; }}
    .type-section {{ margin-bottom: 34px; }}
    .page-block {{ padding: 20px; margin-bottom: 18px; }}
    .page-heading {{ display: flex; gap: 20px; justify-content: space-between; align-items: end; border-bottom: 1px solid var(--border); padding-bottom: 14px; margin-bottom: 18px; }}
    .page-heading h3 {{ margin: 3px 0 0; font-size: 18px; }}
    .page-heading > a {{ font-size: 12px; overflow-wrap: anywhere; text-align: right; }}
    .card-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(460px, 1fr)); gap: 18px; align-items: start; }}
    .image-card {{ border: 1px solid var(--border); border-radius: 8px; background: #fff; overflow: hidden; display: flex; flex-direction: column; min-height: 0; box-shadow: 0 1px 2px rgba(15,23,42,.04); }}
    .image-card:hover {{ box-shadow: var(--shadow); transform: translateY(-1px); transition: box-shadow .18s ease, transform .18s ease; }}
    .thumb {{ display: grid; place-items: center; background: radial-gradient(circle at 50% 40%, #ffffff 0, #f8fafc 42%, #eef2f7 100%); height: clamp(210px, 18vw, 280px); min-height: 210px; border-right: 0; border-bottom: 1px solid var(--border); padding: 12px; overflow: hidden; }}
    .thumb img {{ max-width: 100%; max-height: 100%; width: 100%; height: 100%; object-fit: contain; display: block; filter: drop-shadow(0 12px 22px rgba(15,23,42,.14)); }}
    .card-body {{ padding: 14px; min-width: 0; display: flex; flex-direction: column; gap: 8px; }}
    .card-topline {{ display: flex; justify-content: space-between; gap: 10px; align-items: center; margin-bottom: 8px; }}
    .slot {{ color: var(--accent); font-size: 12px; font-weight: 800; }}
    .ratio {{ background: var(--accent-soft); color: #115e59; border-radius: 999px; padding: 3px 8px; font-size: 12px; }}
    .image-card h3 {{ margin: 0; font-size: 15px; line-height: 1.3; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
    .alt {{ margin: 0; color: var(--muted); font-size: 12px; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
    .design-callout {{ border: 1px solid #99f6e4; background: linear-gradient(180deg, #f0fdfa, #ffffff); border-radius: 8px; padding: 12px; margin: 0; }}
    .design-callout span, .design-callout em {{ display: block; color: #0f766e; font-size: 12px; font-style: normal; font-weight: 700; }}
    .design-callout strong {{ display: block; font-size: 21px; line-height: 1.15; margin: 3px 0; letter-spacing: 0; }}
    .detail-strip {{ display: flex; flex-wrap: wrap; gap: 7px; }}
    .detail-strip span {{ border: 1px solid var(--border); background: #f8fafc; color: var(--muted-foreground); border-radius: 999px; padding: 5px 8px; font-size: 11px; }}
    .guidance {{ color: #334155; font-size: 12px; line-height: 1.45; margin: 0; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
    .links, .badges {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .links a {{ display: inline-flex; align-items: center; gap: 6px; min-height: 34px; border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; background: #fff; color: var(--foreground); font-size: 12px; font-weight: 700; box-shadow: 0 1px 1px rgba(15,23,42,.04); }}
    .links a:hover {{ text-decoration: none; border-color: #99f6e4; background: #f0fdfa; color: #0f766e; }}
    .links a:after {{ content: "\\2197"; font-size: 12px; line-height: 1; }}
    .badge {{ font-size: 11px; border-radius: 999px; padding: 4px 8px; background: #eef2f6; color: #344054; }}
    .badge.warn {{ background: var(--warning-soft); color: var(--warning); }}
    .badge.ok {{ background: var(--success-soft); color: var(--success); }}
    .badge.quiet {{ background: #f2f4f7; color: var(--muted); }}
    .watch-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }}
    .watch-grid ul {{ margin: 0; padding-left: 18px; }}
    .watch-grid li {{ margin-bottom: 10px; color: var(--muted); font-size: 13px; }}
    .watch-grid li strong, .watch-grid li a, .watch-grid li span {{ display: block; }}
    .hidden-by-filter {{ display: none !important; }}
    .footer {{ color: var(--muted); font-size: 12px; text-align: center; margin: 30px 0 10px; }}
    @media (max-width: 980px) {{
      .layout {{ grid-template-columns: 1fr; }}
      aside {{ position: relative; height: auto; }}
      main {{ padding: 18px; width: 100%; overflow: hidden; }}
      .hero, .watch-grid {{ grid-template-columns: 1fr; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0,1fr)); }}
      .card-grid {{ grid-template-columns: 1fr; }}
      .panel {{ max-width: calc(100vw - 36px); }}
    }}
    @media (max-width: 560px) {{
      h1 {{ font-size: 27px; }}
      .thumb {{ height: 230px; padding: 10px; }}
      .spec-grid {{ grid-template-columns: 1fr; }}
      .page-heading {{ display: block; }}
      .page-heading > a {{ display: block; text-align: left; margin-top: 10px; }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <div class="brand-mark">
        <div class="mark"></div>
        <div>
          <strong>Mikano Motors</strong>
          <span>Content Image Guide</span>
        </div>
      </div>
      <nav>
        <a href="#recommended-sizes"><span>Recommended Sizes</span><strong>Guide</strong></a>
        <a href="#watchlist"><span>Watchlist</span><strong>{len(payload['issues'])}</strong></a>
        {render_sidebar(rows)}
      </nav>
      <div class="side-links">
        <a href="{esc(PAGES_URL)}" target="_blank" rel="noreferrer">Open live guide</a>
        <a href="{esc(WORKFLOW_URL)}" target="_blank" rel="noreferrer">Refresh report</a>
        <a href="{esc(payload['sitemapUrl'])}" target="_blank" rel="noreferrer">Open sitemap</a>
        <a href="./mikano-content-image-guide-data.json" target="_blank">Open JSON data</a>
      </div>
    </aside>
    <main>
      <section class="hero">
        <div class="intro">
          <span class="eyebrow">Designer-ready image guide</span>
          <h1>What Mikano Should Design for Each Image</h1>
          <p>Each card gives the designer one clear canvas size and ratio to use. The recommendation is optimized for desktop sharpness first, with a small mobile note for how the same image behaves on phones.</p>
          <div class="metrics">{render_summary_cards(payload)}</div>
        </div>
        <form class="controls" onsubmit="return false">
          <label for="search">Search</label>
          <input id="search" type="search" placeholder="Search page, file, alt text, ratio...">
          <label for="pageType">Page type</label>
          <select id="pageType">
            <option value="">All page types</option>
            {page_type_options}
          </select>
          <label class="check-row"><input id="issuesOnly" type="checkbox"> Show watchlist items only</label>
          <label class="check-row"><input id="missingAltOnly" type="checkbox"> Missing alt text only</label>
          <p class="alt"><strong id="visibleCount">{len(rows)}</strong> visible cards after filtering. Generated {esc(generated)}.</p>
        </form>
      </section>
      {render_recommended_sizes(payload['recommendedSizes'])}
      {render_watchlist(payload)}
      <section class="design-slots">
        <div class="section-title">
          <span>Page-by-page</span>
          <h2>Design Slots</h2>
        </div>
        {render_design_sections(rows)}
      </section>
      <p class="footer">Generated from the rendered public sitemap. Thumbnails are loaded from the live Mikano media server and are not embedded in this file.</p>
    </main>
  </div>
  <script>
    window.__GUIDE_META__ = {data_json};
    const cards = Array.from(document.querySelectorAll('.image-card'));
    const search = document.getElementById('search');
    const pageType = document.getElementById('pageType');
    const issuesOnly = document.getElementById('issuesOnly');
    const missingAltOnly = document.getElementById('missingAltOnly');
    const visibleCount = document.getElementById('visibleCount');

    function applyFilters() {{
      const q = search.value.trim().toLowerCase();
      const type = pageType.value;
      let shown = 0;
      for (const card of cards) {{
        const matchesSearch = !q || card.dataset.search.includes(q);
        const matchesType = !type || card.dataset.pageType === type;
        const matchesIssue = !issuesOnly.checked || card.dataset.issue === 'true';
        const matchesAlt = !missingAltOnly.checked || card.textContent.includes('Missing alt');
        const visible = matchesSearch && matchesType && matchesIssue && matchesAlt;
        card.classList.toggle('hidden-by-filter', !visible);
        if (visible) shown += 1;
      }}
      visibleCount.textContent = shown;
    }}

    [search, pageType, issuesOnly, missingAltOnly].forEach((el) => el.addEventListener('input', applyFilters));
  </script>
</body>
</html>"""


def main():
    data = json.loads(INPUT_JSON.read_text())
    rows = normalize_rows(data)
    recs = recommended_sizes(rows)
    issues = issue_rows(rows)
    payload = write_filtered_json(data, rows, recs, issues)
    OUTPUT_HTML.write_text(render_html(payload))

    print(f"All image rows: {len(data['imageRows'])}")
    print(f"Content guide rows: {len(rows)}")
    print(f"Unique content assets: {payload['uniqueContentAssets']}")
    print(f"Watchlist rows: {len(issues)}")
    print(f"HTML: {OUTPUT_HTML}")
    print(f"JSON: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
