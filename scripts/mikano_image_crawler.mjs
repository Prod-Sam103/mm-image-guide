import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const SITE_MAP = 'https://mikanomotors.com/sitemap.xml';
const MIKANO_ORIGIN = 'https://mikanomotors.com';
const MIKANO_API_ORIGIN = 'https://mm-api.yokeserver.com/api';
const OUTPUT_DIR = path.resolve(process.env.MIKANO_OUTPUT_DIR || 'public');
const OUTPUT_JSON = path.join(OUTPUT_DIR, 'mikano-image-crawl.json');

const VIEWPORTS = [
  { key: 'desktop', label: 'Desktop 1440', width: 1440, height: 1600 },
  { key: 'mobile', label: 'Mobile 390', width: 390, height: 1200, isMobile: true },
];

const WAIT_AFTER_LOAD_MS = 7000;
const PAGE_TIMEOUT_MS = 30000;
const CONCURRENCY = 4;
const CMS_SIZE_PREFERENCE = ['hero', 'large', 'portrait', 'thumbnail'];

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
      let steps = 0;
      const timer = setInterval(() => {
        window.scrollBy(0, distance);
        total += distance;
        steps += 1;
        if (total >= document.body.scrollHeight - window.innerHeight || steps >= 40) {
          clearInterval(timer);
          window.scrollTo(0, 0);
          resolve();
        }
      }, 180);
    });
  });
}

function absoluteUrl(src, base = MIKANO_ORIGIN) {
  if (!src || src.startsWith('data:')) return src || '';
  try {
    return new URL(src, base).href;
  } catch {
    return src;
  }
}

function isCmsImageObject(value) {
  return Boolean(
    value &&
      typeof value === 'object' &&
      typeof value.url === 'string' &&
      typeof value.mimeType === 'string' &&
      value.mimeType.startsWith('image/'),
  );
}

function bestCmsImageVariant(media) {
  for (const sizeName of CMS_SIZE_PREFERENCE) {
    const size = media.sizes?.[sizeName];
    if (size?.url) {
      return {
        url: absoluteUrl(size.url),
        width: Number(size.width || 0),
        height: Number(size.height || 0),
      };
    }
  }

  return {
    url: absoluteUrl(media.url),
    width: Number(media.width || 0),
    height: Number(media.height || 0),
  };
}

function collectCmsMedia(value, found = [], seenObjects = new WeakSet()) {
  if (!value || typeof value !== 'object') return found;
  if (seenObjects.has(value)) return found;
  seenObjects.add(value);

  if (isCmsImageObject(value)) {
    const variant = bestCmsImageVariant(value);
    if (variant.url) {
      found.push({
        order: found.length + 1,
        src: variant.url,
        alt: value.alt || '',
        naturalWidth: variant.width || Number(value.width || 0),
        naturalHeight: variant.height || Number(value.height || 0),
        displayWidth: variant.width || Number(value.width || 0),
        displayHeight: variant.height || Number(value.height || 0),
        visible: true,
        loading: 'cms-api',
        sourceType: 'cms-api',
      });
    }
  }

  if (Array.isArray(value)) {
    for (const item of value) collectCmsMedia(item, found, seenObjects);
    return found;
  }

  for (const item of Object.values(value)) collectCmsMedia(item, found, seenObjects);
  return found;
}

function dedupeImages(images) {
  const bySource = new Map();

  for (const image of images) {
    if (!image.src) continue;
    const key = image.src;
    const current = bySource.get(key);
    const currentArea = (current?.displayWidth || 0) * (current?.displayHeight || 0);
    const nextArea = (image.displayWidth || 0) * (image.displayHeight || 0);
    if (!current || nextArea >= currentArea) {
      bySource.set(key, { ...image, order: current?.order || image.order });
    }
  }

  return [...bySource.values()].sort((a, b) => a.order - b.order);
}

async function collectImages(page) {
  const images = await page.evaluate(() => {
    function absoluteUrl(src) {
      if (!src || src.startsWith('data:')) return src || '';
      try {
        return new URL(src, window.location.href).href;
      } catch {
        return src;
      }
    }

    function visibleRect(element) {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      const width = Math.round(rect.width);
      const height = Math.round(rect.height);
      const visible =
        width > 0 &&
        height > 0 &&
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        Number(style.opacity || '1') > 0;
      return { style, width, height, visible };
    }

    const renderedImages = Array.from(document.images).map((img, index) => {
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
        src: absoluteUrl(src),
        alt: img.alt || '',
        naturalWidth: img.naturalWidth || 0,
        naturalHeight: img.naturalHeight || 0,
        displayWidth: width,
        displayHeight: height,
        visible,
        loading: img.loading || '',
        sourceType: 'dom-img',
      };
    });

    const backgroundImages = [];
    const backgroundUrlPattern = /url\(["']?([^"')]+)["']?\)/g;
    for (const element of Array.from(document.querySelectorAll('body *'))) {
      const { style, width, height, visible } = visibleRect(element);
      if (!visible || !style.backgroundImage || style.backgroundImage === 'none') continue;

      for (const match of style.backgroundImage.matchAll(backgroundUrlPattern)) {
        backgroundImages.push({
          order: renderedImages.length + backgroundImages.length + 1,
          src: absoluteUrl(match[1]),
          alt: element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 100) || '',
          naturalWidth: 0,
          naturalHeight: 0,
          displayWidth: width,
          displayHeight: height,
          visible,
          loading: 'css-background',
          sourceType: 'css-background',
        });
      }
    }

    return [...renderedImages, ...backgroundImages];
  });

  return dedupeImages(images);
}

async function settlePage(page) {
  await page.waitForLoadState('domcontentloaded', { timeout: PAGE_TIMEOUT_MS }).catch(() => {});
  await page.waitForLoadState('networkidle', { timeout: PAGE_TIMEOUT_MS }).catch(() => {});
  await page.waitForTimeout(WAIT_AFTER_LOAD_MS);
  await autoScroll(page);
  await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(1500);
}

async function crawlPage(browser, url, viewport) {
  const page = await browser.newPage({
    viewport: { width: viewport.width, height: viewport.height },
    isMobile: Boolean(viewport.isMobile),
    userAgent:
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36',
  });
  const apiImages = [];
  const apiResponseTasks = [];

  page.on('response', (response) => {
    const responseUrl = response.url();
    if (!responseUrl.startsWith(MIKANO_API_ORIGIN)) return;

    const task = response
      .json()
      .then((json) => {
        apiImages.push(...collectCmsMedia(json));
      })
      .catch(() => {});
    apiResponseTasks.push(task);
  });

  try {
    const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: PAGE_TIMEOUT_MS });
    await settlePage(page);
    await Promise.allSettled(apiResponseTasks);
    const images = dedupeImages([...await collectImages(page), ...apiImages]);
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
      if (!image.src) continue;
      const occurrence = (seenSrc.get(image.src) || 0) + 1;
      seenSrc.set(image.src, occurrence);
      const key = `${url}|${image.src}`;
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
          sourceType: image.sourceType || '',
        };

      if (!existing.originalWidth && image.naturalWidth) existing.originalWidth = image.naturalWidth;
      if (!existing.originalHeight && image.naturalHeight) existing.originalHeight = image.naturalHeight;
      existing.originalRatio = ratioText(existing.originalWidth, existing.originalHeight);
      existing.originalRatioDecimal = ratioDecimal(existing.originalWidth, existing.originalHeight);
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

  if (allRows.length === 0) {
    throw new Error('Crawl completed with 0 image rows. Refusing to build and deploy an empty image guide.');
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
