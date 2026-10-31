// Run explicitly after `bash deploy/local.sh up`.
import assert from 'node:assert/strict';
import { test } from 'node:test';

test('running Docker stack serves SPA routes, static assets and real uncached API', async () => {
  const origin = 'http://localhost:8080';
  for (const path of ['/u/', '/u/account', '/manage/', '/courier/']) {
    const response = await fetch(origin + path);
    assert.equal(response.status, 200, path);
    assert.match(response.headers.get('content-type'), /text\/html/);
    const html = await response.text();
    const script = html.match(/src="(\/assets\/[^\"]+\.js)"/);
    assert.ok(script, `${path}: SPA entry script`);
    assert.equal((await fetch(origin + script[1])).status, 200);
  }
  for (const path of ['/healthz', '/sw.js', '/manifest.webmanifest', '/u/manifest.webmanifest']) {
    assert.equal((await fetch(origin + path)).status, 200, path);
  }
  for (const path of ['/api/v1/health/live', '/api/v1/health/ready', '/api/v1/public/config']) {
    const response = await fetch(origin + path);
    assert.equal(response.status, 200, path);
    assert.match(response.headers.get('cache-control'), /no-store/);
    const cookie = response.headers.get('set-cookie');
    assert.ok(cookie?.includes('csrfToken='));
    assert.ok(!cookie.includes('Secure'), 'HTTP local cookies must be usable');
    assert.ok((await response.json()).data);
  }
});
