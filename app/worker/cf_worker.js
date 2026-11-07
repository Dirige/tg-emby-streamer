/**
 * Cloudflare Worker - URL鉴权 + CDN路由
 *
 * 职责：
 * 1. URL Token 鉴权 (HMAC-SHA256)
 * 2. 路由到 Stream Server
 * 3. CDN 缓存
 * 4. 防盗链
 *
 * 不负责：
 * - 读取 Telegram 视频（由 FastAPI 完成）
 * - 视频转码
 *
 * 环境变量（在 Cloudflare Dashboard 设置）：
 * - STREAM_SERVER: 你的 FastAPI 流媒体服务器地址
 * - WORKER_SECRET: HMAC 签名密钥
 */

export default {
  async fetch(request, env) {
    const STREAM_SERVER = env.STREAM_SERVER || 'https://your-stream-server.example.com';
    const WORKER_SECRET = env.WORKER_SECRET || 'your_worker_secret_here';

    const url = new URL(request.url);

    if (url.pathname === '/' || url.pathname === '/health') {
      return new Response(JSON.stringify({ service: 'CF Worker', status: 'ok' }), {
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const streamMatch = url.pathname.match(/^\/stream\/(\d+)$/);
    if (!streamMatch) {
      return new Response('Not Found', { status: 404 });
    }

    const messageId = streamMatch[1];
    const token = url.searchParams.get('token');

    if (!token) {
      return new Response('Missing token', { status: 403 });
    }

    const valid = await verifyToken(messageId, token, WORKER_SECRET);
    if (!valid) {
      return new Response('Invalid or expired token', { status: 403 });
    }

    const streamUrl = `${STREAM_SERVER}/stream/${messageId}`;
    const headers = new Headers();

    const rangeHeader = request.headers.get('Range');
    if (rangeHeader) {
      headers.set('Range', rangeHeader);
    }

    const ifNoneMatch = request.headers.get('If-None-Match');
    if (ifNoneMatch) {
      headers.set('If-None-Match', ifNoneMatch);
    }

    const response = await fetch(streamUrl, {
      method: request.method,
      headers,
    });

    const responseHeaders = new Headers(response.headers);
    responseHeaders.set('Access-Control-Allow-Origin', '*');
    responseHeaders.set('Cache-Control', 'public, max-age=3600');

    return new Response(response.body, {
      status: response.status,
      headers: responseHeaders,
    });
  },
};

async function verifyToken(messageId, token, secret) {
  try {
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw',
      encoder.encode(secret),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign']
    );

    const now = Math.floor(Date.now() / 1000);
    const window = 3600;

    for (let offset = 0; offset <= 1; offset++) {
      const ts = now - offset * window;
      const message = `${messageId}:${ts}`;
      const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(message));
      const expectedToken = btoa(String.fromCharCode(...new Uint8Array(signature)))
        .replace(/\+/g, '-')
        .replace(/\//g, '_')
        .replace(/=+$/, '');

      if (token === expectedToken) {
        return true;
      }
    }

    return false;
  } catch {
    return false;
  }
}

export async function generateToken(messageId, secret) {
  const now = Math.floor(Date.now() / 1000);
  const message = `${messageId}:${now}`;
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(message));
  return btoa(String.fromCharCode(...new Uint8Array(signature)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}
