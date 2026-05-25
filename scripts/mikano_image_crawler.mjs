import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const SITE_MAP = 'https://mikanomotors.com/sitemap.xml';
const OUTPUT_DIR = path.resolve(process.env.MIKANO_OUTPUT_DIR || 'public');
const OUTPUT_JSON = path.join(OUTPUT_DIR, 'mikano-image-crawl.json');

const VIEWPORTS = [
  { key: 'desktop', label: 'Desktop 1440', width: 1440, height: 1600 },
  { key: 'mobile', label: 'Mobile 390', width: 390, height: 1200, isMobile: true },
];

const WAIT_AFTER_LOAD_MS = 7000;
const PAGE_TIMEOUT_MS = 30000;
const CONCURRENCY = 4;

function pageType(url) {
  const pathname = new URL(url).pathname;
  if (pathname === '/') return 'home';
  if (pathname === '/brands') return 'brand listing';
  if (pathname.startsWith('/brands/')) return 'brand';
  if (pathname === '/vehicles') return 'vehicle listing';
  if (pathname.startsWith('/cars/')) return 'vehicle';
  if (pathname === '/blog') return 'blog listing';
  if (pathname.startsWith('/blog/')) return 'blog';
  if (pathname === '/news-events') return 'news/event listing';
  if (pathname.startsWith('/news-events/')) return 'news/event';
  if (pathname === '/promos') return 'promo/listing';
  if (pathname.startsWith('/promos/')) return 'promo/listing';
  if (pathname.startsWith('/page/')) return 'static page';
  return 'other';
}

function fileNameFromSrc(src) {
  if (!src) return '';
  if (src.startsWith('data:')) return '[inline data image]';
  try {
    const pathname = new URL(src).pathname;
    return decodeURIComponent(pathname.split('/').filter(Boolean).pop() || '');
  } catch {
    return src.split('/').pop() || '';
  }
}

function shortSrc(src) {
  if (!src) return '';
  if (src.startsWith('data:')) {
    const mime = src.match(/^data:([^;]+)/)?.[1] || 'data image';
    return `data:${mime};base64,[inline image omitted]`;
  }
  return src;
}

function gcd(a, b) {
  a = Math.abs(Math.round(a));
  b = Math.abs(Math.round(b));
  while (b) [a, b] = [b, a % b];
  return a || 1;
}

function ratioText(width, height) {
  if (!width || !height) return '';
  const divisor = gcd(width, height);
  return `${Math.round(width / divisor)}:${Math.round(height / divisor)}`;
}

function ratioDecimal(width, height) {
  if (!width || !height) return null;
  return Number((width / height).toFixed(4));
}

function recommendation(asset, display) {
  if (!asset?.width || !asset?.height || !display?.width || !display?.height) return '';
  const assetRatio = asset.width / asset.height;
  const displayRatio = display.width / display.height;
  const delta = Math.abs(assetRatio - displayRatio) / displayRatio;

  if (display.width >= 900 && display.height >= 250) return 'Hero/banner slot: prepare artwork at the rendered ratio or larger.';
  if (delta > 0.18) return 'Asset ratio differs from display slot; image may crop, stretch, or leave awkward spacing.';
  if (asset.width < display.width || asset.height < display.height) return 'Uploaded asset is smaller than rendered size; use a higher-resolution image.';
  return '';
}

function classifyAsset(src, fileName, alt, visible, repeatCount) {
  const normalized = `${src} ${fileName} ${alt}`.toLowerCase();
  if (src.startsWith('data:')) return 'decorative/data image';
  if (normalized.includes('logo') || normalized.includes('favicon')) return 'logo/icon';
  if (repeatCount > 8 && !normalized.includes('media/')) return 'repeated global asset';
  if (!visible) return 'hidden image';
  return 'content image';
}

async function fetchSitemapUrls() {
  const response = await fetch(SITE_MAP);
  if (!response.ok) throw new Error(`Failed to fetch sitemap: ${response.status}`);
  const xml = await response.text();
  return [...xml.matchAll(/<loc>(.*?)<\/loc>/g)].map((match) => match[1].trim());
}

async function autoScroll(page) {
  await page.evaluate(async () => {
    await new Promise((resolve) => {
      let total = 0;
      const distance = Math.max(300, Math.floor(window.innerHeight * 0.7));
      const timer = setInterval(() => {
        window.scrollBy(0, distance);
        total += distance;
        if (total >= document.body.scrollHeight - window.innerHeight) {
          clearInterval(timer);
          window.scrollTo(0, 0);
          resolve();
        }
      }, 180);
    });
  });
}

async function collectImages(page) {
  return page.evaluate(() =>
    Array.from(document.images).map((img, index) => {
      const rect = img.getBoundingClientRect();
      const style = window.getComputedStyle(img);
      const width = Math.round(rect.width);
      const height = Math.round(rect.height);
      const src = img.currentSrc || img.src || '';
      const visible =
        width > 0 &&
        height > 0 &&
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        Number(style.opacity || '1') > 0;

      return {
        order: index + 1,
        src,
        alt: img.alt || '',
        naturalWidth: img.naturalWidth || 0,
        naturalHeight: img.naturalHeight || 0,
        displayWidth: width,
        displayHeight: height,
        visible,
        loading: img.loading || '',
      };
    }),
  );
}

async function crawlPage(browser, url, viewport) {
  const page = await browser.newPage({
    viewport: { width: viewport.width, height: viewport.height },
    isMobile: Boolean(viewport.isMobile),
    userAgent:
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36',
  });

  try {
    const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: PAGE_TIMEOUT_MS });
    await page.waitForTimeout(WAIT_AFTER_LOAD_MS);
    await autoScroll(page);
    await page.waitForTimeout(1000);
    const images = await collectImages(page);
    return { ok: true, status: response?.status() || null, finalUrl: page.url(), images };
  } catch (error) {
    return { ok: false, error: error.message, images: [] };
  } finally {
    await page.close();
  }
}

async function runPool(items, worker) {
  const results = new Array(items.length);
  let next = 0;

  async function runWorker() {
    while (next < items.length) {
      const index = next++;
      results[index] = await worker(items[index], index);
    }
  }

  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, items.length) }, runWorker));
  return results;
}

function mergeViewportRows(url, viewportResults) {
  const byKey = new Map();

  for (const viewport of VIEWPORTS) {
    const result = viewportResults[viewport.key];
    const seenSrc = new Map();

    for (const image of result.images || []) {
      const occurrence = (seenSrc.get(image.src) || 0) + 1;
      seenSrc.set(image.src, occurrence);
      const key = `${url}|${image.src}|${occurrence}`;
      const existing =
        byKey.get(key) ||
        {
          pageUrl: url,
          pageType: pageType(url),
          imageOrder: image.order,
          sourceUrl: shortSrc(image.src),
          sourceIdentity: image.src,
          fileName: fileNameFromSrc(image.src),
          altText: image.alt,
          originalWidth: image.naturalWidth,
          originalHeight: image.naturalHeight,
          originalRatio: ratioText(image.naturalWidth, image.naturalHeight),
          originalRatioDecimal: ratioDecimal(image.naturalWidth, image.naturalHeight),
          desktopWidth: '',
          desktopHeight: '',
          desktopRatio: '',
          desktopRatioDecimal: '',
          mobileWidth: '',
          mobileHeight: '',
          mobileRatio: '',
          mobileRatioDecimal: '',
          visibilityStatus: '',
          loading: image.loading,
        };

      existing[`${viewport.key}Width`] = image.displayWidth;
      existing[`${viewport.key}Height`] = image.displayHeight;
      existing[`${viewport.key}Ratio`] = ratioText(image.displayWidth, image.displayHeight);
      existing[`${viewport.key}RatioDecimal`] = ratioDecimal(image.displayWidth, image.displayHeight);
      existing.visibilityStatus = image.visible
        ? 'visible'
        : image.displayWidth === 0 || image.displayHeight === 0
          ? 'zero-size'
          : 'hidden';
      existing.imageOrder = Math.min(existing.imageOrder, image.order);
      byKey.set(key, existing);
    }
  }

  return [...byKey.values()].sort((a, b) => a.imageOrder - b.imageOrder);
}

async function main() {
  await fs.mkdir(OUTPUT_DIR, { recursive: true });
  const urls = await fetchSitemapUrls();
  const browser = await chromium.launch({ headless: true });
  const pageResults = [];

  console.log(`Found ${urls.length} sitemap URLs. Crawling ${VIEWPORTS.length} viewport sizes...`);

  const crawlResults = await runPool(urls, async (url, index) => {
    console.log(`[${index + 1}/${urls.length}] ${url}`);
    const viewportResults = {};
    for (const viewport of VIEWPORTS) {
      viewportResults[viewport.key] = await crawlPage(browser, url, viewport);
    }
    return { url, pageType: pageType(url), viewportResults };
  });

  await browser.close();

  const allRows = [];
  const failures = [];
  const noImagePages = [];

  for (const pageResult of crawlResults) {
    const rows = mergeViewportRows(pageResult.url, pageResult.viewportResults);
    if (!rows.length) noImagePages.push(pageResult.url);

    for (const viewport of VIEWPORTS) {
      const result = pageResult.viewportResults[viewport.key];
      if (!result.ok) {
        failures.push({
          pageUrl: pageResult.url,
          pageType: pageResult.pageType,
          viewport: viewport.label,
          error: result.error,
        });
      }
    }

    pageResults.push({ ...pageResult, imageCount: rows.length });
    allRows.push(...rows);
  }

  const repeatCounts = new Map();
  for (const row of allRows) repeatCounts.set(row.sourceIdentity, (repeatCounts.get(row.sourceIdentity) || 0) + 1);

  for (const row of allRows) {
    const desktopDisplay = { width: row.desktopWidth || row.mobileWidth, height: row.desktopHeight || row.mobileHeight };
    row.assetType = classifyAsset(
      row.sourceIdentity,
      row.fileName,
      row.altText,
      row.visibilityStatus === 'visible',
      repeatCounts.get(row.sourceIdentity) || 0,
    );
    row.recommendation = recommendation(
      { width: row.originalWidth, height: row.originalHeight },
      desktopDisplay,
    );
  }

  const uniqueAssets = [...repeatCounts.keys()].map((sourceIdentity) => {
    const sample = allRows.find((row) => row.sourceIdentity === sourceIdentity);
    return {
      sourceUrl: sample.sourceUrl,
      fileName: sample.fileName,
      originalWidth: sample.originalWidth,
      originalHeight: sample.originalHeight,
      originalRatio: sample.originalRatio,
      originalRatioDecimal: sample.originalRatioDecimal,
      assetType: sample.assetType,
      occurrenceCount: repeatCounts.get(sourceIdentity),
      samplePageUrl: sample.pageUrl,
      altText: sample.altText,
    };
  });

  const payload = {
    generatedAt: new Date().toISOString(),
    sitemapUrl: SITE_MAP,
    viewportStandards: VIEWPORTS.map(({ key, label, width, height }) => ({ key, label, width, height })),
    pageResults,
    imageRows: allRows,
    uniqueAssets,
    failures,
    noImagePages,
  };

  await fs.writeFile(OUTPUT_JSON, JSON.stringify(payload, null, 2));
  console.log(`Saved ${allRows.length} image rows and ${uniqueAssets.length} unique assets to ${OUTPUT_JSON}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
