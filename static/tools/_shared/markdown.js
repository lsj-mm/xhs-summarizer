/* 工具箱共用的轻量 Markdown 渲染：标题、列表、引用、代码块、粗体、行内代码、链接。
 * 只用于模型回复的展示；所有文本先转义，再插入固定的 HTML 标签，避免 XSS。
 * 用法：const html = window.DSH_MD.toHtml(markdownText);
 */
(function (root) {
  'use strict';

  function escapeHtml(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function inline(text) {
    let s = escapeHtml(text);
    s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
    return s;
  }

  function toHtml(markdown) {
    const lines = String(markdown || '').replace(/\r\n?/g, '\n').split('\n');
    let out = '';
    let para = [];
    let list = null;

    function flushPara() {
      if (para.length) {
        out += '<p>' + para.map(inline).join('<br>') + '</p>';
        para = [];
      }
    }
    function closeList() {
      if (list) {
        out += '</' + list + '>';
        list = null;
      }
    }

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      if (/^\s*```/.test(line)) {
        flushPara(); closeList();
        const code = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) { code.push(lines[i]); i++; }
        out += '<pre><code>' + escapeHtml(code.join('\n')) + '</code></pre>';
        continue;
      }

      if (!line.trim()) { flushPara(); closeList(); continue; }

      if (/^\s*(---|\*\*\*)\s*$/.test(line)) { flushPara(); closeList(); out += '<hr>'; continue; }

      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        flushPara(); closeList();
        const level = Math.min(4, heading[1].length + 1);
        out += '<h' + level + '>' + inline(heading[2]) + '</h' + level + '>';
        continue;
      }

      const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
      if (bullet) {
        flushPara();
        if (list !== 'ul') { closeList(); out += '<ul>'; list = 'ul'; }
        out += '<li>' + inline(bullet[1]) + '</li>';
        continue;
      }

      const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (numbered) {
        flushPara();
        if (list !== 'ol') { closeList(); out += '<ol>'; list = 'ol'; }
        out += '<li>' + inline(numbered[1]) + '</li>';
        continue;
      }

      const quote = line.match(/^\s*>\s?(.*)$/);
      if (quote) {
        flushPara(); closeList();
        out += '<blockquote>' + inline(quote[1]) + '</blockquote>';
        continue;
      }

      para.push(line);
    }

    flushPara(); closeList();
    return out;
  }

  root.DSH_MD = { toHtml: toHtml, escapeHtml: escapeHtml };
})(typeof globalThis !== 'undefined' ? globalThis : this);
