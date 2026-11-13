// 网页版聊天记录：IndexedDB（图片/文件的 base64 也一起存）。
// 每个浏览器/站点一份，和桌面端的本地 SQLite 互不影响。
const DB_NAME = 'petclub-history';
const STORE = 'messages';
export const MAX_HISTORY = 200;

let dbPromise = null;

function openDB() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve) => {
    if (!window.indexedDB) {
      resolve(null);
      return;
    }
    let req;
    try {
      req = indexedDB.open(DB_NAME, 1);
    } catch (_) {
      resolve(null);
      return;
    }
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
        store.createIndex('conv', 'conv');
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
  });
  return dbPromise;
}

function requestPromise(req) {
  return new Promise((resolve) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
  });
}

export async function initHistory() {
  await openDB();
}

export async function loadHistory(limit = MAX_HISTORY) {
  const db = await openDB();
  if (!db) return {};
  try {
    const tx = db.transaction(STORE, 'readonly');
    const rows = await requestPromise(tx.objectStore(STORE).getAll());
    if (!Array.isArray(rows)) return {};
    rows.sort((a, b) => (a.id || 0) - (b.id || 0));
    const out = {};
    for (const row of rows) {
      const conv = row.conv || 'all';
      const entry = {
        kind: row.kind || 'msg',
        who: row.who || '',
        text: row.text || '',
        mt: row.mt || 'text',
        char: row.char || '',
        img: row.img || '',
        name: row.name || '',
        size: row.size || 0,
        ts: row.ts || 0,
      };
      if (!out[conv]) out[conv] = [];
      out[conv].push(entry);
    }
    for (const conv of Object.keys(out)) {
      out[conv] = out[conv].slice(-limit);
    }
    return out;
  } catch (_) {
    return {};
  }
}

export async function saveHistory(conv, entry) {
  const db = await openDB();
  if (!db) return;
  try {
    const tx = db.transaction(STORE, 'readwrite');
    const store = tx.objectStore(STORE);
    store.add({
      conv,
      ts: entry.ts || Date.now() / 1000,
      kind: entry.kind || 'msg',
      who: entry.who || '',
      text: entry.text || '',
      mt: entry.mt || 'text',
      char: entry.char || '',
      img: entry.img || '',
      name: entry.name || '',
      size: entry.size || 0,
    });
    tx.oncomplete = () => trim(conv);
  } catch (_) {
    // 隐私模式 / 配额不足：这次不落盘，聊天仍然正常
  }
}

async function trim(conv) {
  const db = await openDB();
  if (!db) return;
  try {
    // 先用只读事务拿 key，再用独立写事务删；不要在 await 之后继续用
    // 同一个 IndexedDB 事务，那个事务可能已经自动提交。
    const keys = await new Promise((resolve) => {
      const tx = db.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).index('conv').getAllKeys(IDBKeyRange.only(conv));
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => resolve([]);
    });
    if (!Array.isArray(keys) || keys.length <= MAX_HISTORY) return;
    const tx = db.transaction(STORE, 'readwrite');
    const store = tx.objectStore(STORE);
    const extra = keys.length - MAX_HISTORY;
    for (let i = 0; i < extra; i++) store.delete(keys[i]);
  } catch (_) {
    // 清理失败不影响聊天
  }
}

export async function clearHistory() {
  const db = await openDB();
  if (!db) return;
  try {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).clear();
  } catch (_) {
    // 忽略
  }
}
