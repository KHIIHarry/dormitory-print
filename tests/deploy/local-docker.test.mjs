import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, statSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

test('local Docker entrypoint renders an isolated full stack and preserves credentials', () => {
  const dir = mkdtempSync(join(tmpdir(), 'dormprint-compose-'));
  const envFile = join(dir, '.env.local');
  try {
    const render = () => {
      const result = spawnSync('bash', ['deploy/local.sh', '--profile', '*', 'config', '--format', 'json'], {
        cwd: new URL('../../', import.meta.url),
        env: { ...process.env, DORMPRINT_LOCAL_ENV_FILE: envFile },
        encoding: 'utf8',
      });
      assert.equal(result.status, 0, result.stderr);
      return JSON.parse(result.stdout);
    };
    const config = render();
    const original = readFileSync(envFile, 'utf8');
    assert.equal(statSync(envFile).mode & 0o777, 0o600);
    assert.equal(config.name, 'dormprint-local');
    for (const name of ['api', 'file-worker', 'business-worker', 'beat', 'migrate', 'init-admin', 'doctor']) {
      const app = config.services[name];
      assert.equal(app.image, 'dormprint-api:local');
      assert.equal(app.environment.DORMPRINT_ENVIRONMENT, 'development');
      assert.equal(app.environment.DORMPRINT_SECURE_COOKIES, 'false');
      assert.equal(app.environment.DORMPRINT_PUBLIC_ORIGIN, 'http://localhost:8080');
      assert.ok(app.environment.DORMPRINT_SECRET_KEY.length >= 32);
      assert.ok(!app.ports?.length);
    }
    const proxy = config.services.proxy;
    assert.equal(proxy.image, 'dormprint-web:local');
    assert.equal(proxy.ports[0].host_ip, '127.0.0.1');
    assert.equal(String(proxy.ports[0].published), '8080');
    assert.ok(!proxy.volumes?.length);
    assert.equal(proxy.environment.NGINX_ENVSUBST_OUTPUT_DIR, '/tmp/conf.d');
    for (const name of ['postgres', 'redis']) assert.ok(!config.services[name].ports?.length);
    assert.deepEqual(Object.keys(config.services['file-worker'].networks), ['internal']);
    for (const name of ['file-worker', 'business-worker']) {
      assert.match(config.services[name].healthcheck.test.join(' '), /inspect ping/);
    }
    assert.match(config.services.beat.healthcheck.test.join(' '), /celerybeat.pid/);
    assert.equal(config.networks.internal.internal, true);
    assert.deepEqual(config.services.backup.profiles, ['backup']);
    render();
    assert.equal(readFileSync(envFile, 'utf8'), original);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
