const fs = require('fs')
const path = require('path')
const { chromium } = require('playwright')

function readArg(flag, fallback = '') {
  const index = process.argv.indexOf(flag)
  if (index === -1 || index + 1 >= process.argv.length) return fallback
  return process.argv[index + 1]
}

async function main() {
  const url = readArg('--url', 'http://127.0.0.1:8080')
  const expectText = readArg('--expect-text', '')
  const expectTitle = readArg('--expect-title', '')
  const screenshotPath = readArg('--screenshot', 'output/browser-check.png')

  fs.mkdirSync(path.dirname(screenshotPath), { recursive: true })

  const browser = await chromium.launch({ headless: true })

  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1200 },
    })

    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })

    if (expectTitle) {
      const title = await page.title()
      if (!title.includes(expectTitle)) {
        throw new Error(`Expected page title to include "${expectTitle}", got "${title}"`)
      }
    }

    if (expectText) {
      await page.waitForFunction(
        (value) => document.body && document.body.innerText.includes(value),
        expectText,
        { timeout: 30000 }
      )
    }

    await page.screenshot({
      path: screenshotPath,
      fullPage: true,
    })

    const title = await page.title()
    const bodyText = await page.locator('body').innerText()
    console.log(
      JSON.stringify(
        {
          ok: true,
          url,
          title,
          matchedText: expectText || null,
          screenshot: screenshotPath,
          bodyPreview: bodyText.slice(0, 400),
        },
        null,
        2
      )
    )
  } finally {
    await browser.close()
  }
}

main().catch((error) => {
  console.error(error.stack || String(error))
  process.exit(1)
})
