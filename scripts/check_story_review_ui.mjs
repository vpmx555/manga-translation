// Optional browser smoke checks: uses an existing Playwright module and browser.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, index, all) => {
  if (value.startsWith('--')) pairs.push([value.slice(2), all[index + 1]]);
  return pairs;
}, []));
if (!args['review-dir'] || !args['playwright-module'] || !args.browser) {
  throw new Error('Required: --review-dir <folder> --playwright-module <index.mjs> --browser <executable>');
}
const output = path.resolve(args['review-dir']);
const qa = path.join(output, 'qa');
fs.mkdirSync(qa, {recursive: true});
const {chromium} = await import(pathToFileURL(path.resolve(args['playwright-module'])).href);
const browser = await chromium.launch({executablePath: args.browser, headless: true});
const context = await browser.newContext({viewport: {width: 1536, height: 1040}, deviceScaleFactor: 1});
const page = await context.newPage();
const errors = [], checks = [], failedImages = [];
let completed = false;
page.on('pageerror', error => errors.push(error.message));
page.on('response', response => {if (response.request().resourceType() === 'image' && !response.ok()) failedImages.push(response.url());});
const open = async file => page.goto(pathToFileURL(path.join(output, file)).href);
const screenshot = name => page.screenshot({path: path.join(qa, name + '.png'), animations: 'disabled'});
const check = (name, condition) => {assert.ok(condition, name); checks.push(name);};
try {
  await open('index.html');
  const counts = await page.evaluate(() => {
    const d = JSON.parse(document.getElementById('data').textContent);
    return {scenes: d.scenes.length, events: d.scenes.flatMap(s => s.events).length};
  });
  check('All scenes appear in the connected map', await page.locator('[data-map-scene]').count() === counts.scenes);
  check('All events appear in the connected map', await page.locator('.event-node').count() === counts.events);
  check('Navigation exposes all four hierarchy levels', await page.locator('[role=treeitem][aria-level="4"]').count() > 0);
  await screenshot('desktop-map');
  await page.getByRole('searchbox').fill('a-query-that-does-not-occur-9381');
  check('Empty search has a recovery action', await page.getByRole('heading', {name: 'No branches in view.'}).isVisible());
  await page.getByRole('button', {name: 'Show the whole chapter'}).click();
  check('Clearing filters restores the map', await page.locator('.event-node').count() === counts.events);
  await page.getByRole('button', {name: 'Collapse all scenes', exact: true}).click();
  check('Tree collapses without losing scene nodes', await page.locator('[role=treeitem][aria-level="3"]').count() === 0);
  await page.getByRole('button', {name: 'Expand all scenes', exact: true}).click();
  check('Tree expands to every event', await page.locator('[role=treeitem][aria-level="3"]').count() === counts.events);
  await page.goto(pathToFileURL(path.join(output, 'analyze/index.html')).href);
  const sourceBefore = await page.locator('#data').textContent();
  await page.locator('.stat-card.alert').click();
  check('Attention filter excludes unrelated events', await page.locator('.event-node:not(.attention)').count() === 0);
  await page.locator('[data-action=filter][data-filter=all]').click();
  const targetId = await page.evaluate(() => {
    const d = JSON.parse(document.getElementById('data').textContent);
    const es = d.scenes.flatMap(s => s.events);
    return (es.find(e => e.event_id === 'scene:8:event:3') || es.find(e => e.image_packets?.length) || es[0]).event_id;
  });
  await page.locator('.event-node[data-id="' + targetId + '"]').click();
  check('Event opens the reading room', await page.getByRole('tab', {name: /^Utterances/}).isVisible());
  check('Saved decisions display with source text', await page.locator('.utterance-card').count() > 0);
  if (await page.locator('.evidence-image img').count()) {
    await page.waitForFunction(() => Array.from(document.querySelectorAll('.evidence-image img')).every(i => i.complete && i.naturalWidth > 0));
    check('Retained evidence image loads', await page.locator('.evidence-image img').evaluate(i => i.naturalWidth > 0));
    await page.getByRole('button', {name: 'Enlarge source evidence'}).click();
    check('Evidence opens in an accessible image dialog', await page.locator('#image-dialog').evaluate(d => d.open));
    await page.getByRole('button', {name: 'Actual size', exact: true}).click();
    check('Image can be inspected at actual size', await page.locator('#dialog-body').evaluate(d => d.classList.contains('expanded')));
    await page.getByRole('button', {name: 'Close evidence image'}).click();
  }
  await screenshot('desktop-reading');
  await page.getByRole('button', {name: 'Mark reviewed', exact: true}).click();
  check('Review mark updates personal progress', (await page.locator('#progress-label').textContent()).startsWith('1 /'));
  check('Review mark does not mutate embedded source results', sourceBefore === await page.locator('#data').textContent());
  await page.reload();
  await page.locator('.event-node[data-id="' + targetId + '"]').click();
  check('Review progress survives a reload', await page.getByRole('button', {name: 'Reviewed', exact: true}).getAttribute('aria-pressed') === 'true');
  await page.getByRole('tab', {name: /^Details/}).click();
  check('Details remain available without crowding the reading view', await page.getByText('Event references', {exact: true}).isVisible());
  await page.setViewportSize({width: 390, height: 844});
  await open('index.html');
  check('Mobile page does not overflow its viewport', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await screenshot('mobile-map');
  await page.getByRole('button', {name: 'Open chapter navigation'}).click();
  await screenshot('mobile-tree');
  const first = page.locator('[role=treeitem]').first();
  await first.focus();
  await page.keyboard.press('ArrowDown');
  check('Tree navigation supports keyboard traversal', await page.evaluate(() => document.activeElement.getAttribute('aria-level') === '2'));
  check('No uncaught browser errors', errors.length === 0);
  check('No failed evidence image responses', failedImages.length === 0);
  const report = {checks, errors, failedImages, screenshots: ['desktop-map', 'desktop-reading', 'mobile-map', 'mobile-tree'].map(n => n + '.png')};
  fs.writeFileSync(path.join(qa, 'browser-checks.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({passed: checks.length, errors, screenshots: report.screenshots}));
  completed = true;
} catch (error) {
  console.error(error.stack || String(error));
  process.exitCode = 1;
} finally {
  // Bound cleanup if Edge leaves driver/background handles open after closing.
  // An assertion failure must still produce a failing exit status.
  const shutdown = setTimeout(() => process.exit(completed ? 0 : 1), 4000);
  shutdown.unref();
  await browser.close();
  clearTimeout(shutdown);
  process.exit(completed ? 0 : 1);
}
