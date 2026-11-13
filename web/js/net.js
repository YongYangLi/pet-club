// 浏览器 WebSocket 客户端：断线重连 + JSON 消息分发。
// 心跳由网关负责（它替浏览器给服务端发 ping），这里只做连接管理。

export class Net {
  constructor({ onMessage, onOpen, onClose }) {
    this.onMessage = onMessage;
    this.onOpen = onOpen;
    this.onClose = onClose;
    this.ws = null;
    this.connected = false;
    this.stopped = false;
    this.backoff = 3;
    this.timer = null;
  }

  connect() {
    this.stopped = false;
    this._open();
  }

  _open() {
    if (this.stopped) return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN
      || this.ws.readyState === WebSocket.CONNECTING)) return;

    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    this.ws = ws;

    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.connected = true;
      this.backoff = 3;
      this.onOpen && this.onOpen();
    };

    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (_) {
        return;
      }
      if (msg && typeof msg === 'object') this.onMessage && this.onMessage(msg);
    };

    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.connected = false;
      this.onClose && this.onClose();
      if (!this.stopped) this._schedule();
    };

    ws.onerror = () => {
      try { ws.close(); } catch (_) {}
    };
  }

  _schedule() {
    clearTimeout(this.timer);
    const delay = this.backoff;
    this.backoff = Math.min(this.backoff * 1.6, 20);
    this.timer = setTimeout(() => this._open(), delay * 1000);
  }

  send(obj) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    try {
      this.ws.send(JSON.stringify(obj));
      return true;
    } catch (_) {
      return false;
    }
  }

  reconnectNow() {
    if (this.connected || this.stopped) return;
    clearTimeout(this.timer);
    this._open();
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    const ws = this.ws;
    this.ws = null;
    this.connected = false;
    try { ws && ws.close(); } catch (_) {}
  }
}
