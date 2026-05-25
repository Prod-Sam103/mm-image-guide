# Mikano Motors Image Guide

Auto-refreshing content image design guide for the public Mikano Motors website.

The guide crawls `https://mikanomotors.com/sitemap.xml`, renders the Vue pages in Chromium, extracts content/CMS image dimensions, and publishes a designer-friendly HTML reference.

## Live Guide

After GitHub Pages is enabled and the workflow runs successfully:

https://prod-sam103.github.io/mm-image-guide/

## Refresh Schedule

- Weekly: Monday 7:00 AM Lagos time
- Manual: run **Refresh Image Guide** from the Actions tab

Manual workflow:

https://github.com/Prod-Sam103/mm-image-guide/actions/workflows/refresh-image-guide.yml

## Local Run

```bash
npm install
npx playwright install chromium
npm run refresh
```

The local output is written to `public/index.html` and `public/mikano-content-image-guide-data.json`.

## Outputs

- `public/index.html`: the shareable design guide
- `public/mikano-content-image-guide-data.json`: filtered guide data

The raw crawl file `public/mikano-image-crawl.json` is generated during refresh but excluded from deployment.
