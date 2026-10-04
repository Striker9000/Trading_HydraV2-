const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const path = require('path');
(async () => {
  const b = await chromium.launch();
  const jobs = [
    { f: 'flyer', w: 816, h: 1056, pages: 1, pdf: { width: '8.5in', height: '11in' } },
    { f: 'setup-card', w: 576, h: 384, pages: 2, pdf: { width: '6in', height: '4in' } },
    { f: 'sticker', w: 288, h: 288, pages: 1, pdf: { width: '3in', height: '3in' }, scale: 3.125 },
  ];
  for (const j of jobs) {
    const p = await b.newPage({ viewport: { width: j.w, height: j.h * j.pages }, deviceScaleFactor: j.scale || 2 });
    await p.goto('file://' + path.resolve(j.f + '.built.html'));
    await p.evaluate(() => document.fonts.ready);
    await p.pdf({ path: `out/${j.f}.pdf`, ...j.pdf, printBackground: true, preferCSSPageSize: true });
    for (let i = 0; i < j.pages; i++)
      await p.screenshot({ omitBackground: true, path: `out/${j.f}${j.pages > 1 ? '-' + (i ? 'back' : 'front') : ''}.png`, clip: { x: 0, y: i * j.h, width: j.w, height: j.h } });
    const fonts = await p.evaluate(() => [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family + ' ' + f.weight));
    const over = await p.evaluate(() => [...document.querySelectorAll('body *')].filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflow === 'visible').map(e => e.className || e.tagName).slice(0, 8));
    console.log(j.f, 'fonts:', fonts.join(', '), '| overflow:', over.join(',') || 'none');
  }
  await b.close();
})();
