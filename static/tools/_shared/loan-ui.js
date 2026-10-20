/* 房贷 / 车贷两个工具共用的结果渲染。依赖 /static/tools/_shared/finance.js（提供 window.FIN）。
 * 把「结果卡片 + 按年汇总表 + 每期明细表」的生成集中在这里，两个页面只负责输入与取值。 */
window.LoanUI = (function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
  }

  /** 通用结果卡片：cards = [{k, v, cls}] */
  function cardsHtml(cards) {
    return (cards || []).map(c =>
      `<div class="res ${c.cls || ''}"><div class="k">${esc(c.k)}</div><div class="v">${esc(c.v)}</div></div>`
    ).join('');
  }

  /** 把多笔贷款的年度数据按年合并（组合贷用） */
  function yearRowsMerged(loans) {
    const maxYears = Math.max(...(loans || []).map(l => l.byYear.length), 0);
    const rows = [];
    for (let y = 0; y < maxYears; y++) {
      const chunk = loans.map(l => l.byYear[y]).filter(Boolean);
      rows.push({
        year: y + 1,
        payment: FIN.round2(chunk.reduce((s, x) => s + x.payment, 0)),
        principal: FIN.round2(chunk.reduce((s, x) => s + x.principal, 0)),
        interest: FIN.round2(chunk.reduce((s, x) => s + x.interest, 0)),
        balance: FIN.round2(chunk.reduce((s, x) => s + x.balance, 0)),
      });
    }
    return rows;
  }

  /** 用「年度行数据」渲染表格（组合贷合并后也用它） */
  function yearTableRowsHtml(rows) {
    const body = (rows || []).map(y => `<tr>
        <td>第 ${y.year} 年</td>
        <td class="num">${FIN.money(y.payment)}</td>
        <td class="num">${FIN.money(y.principal)}</td>
        <td class="num">${FIN.money(y.interest)}</td>
        <td class="num">${FIN.money(y.balance)}</td>
      </tr>`).join('');
    return `<table>
      <thead><tr><th>时间</th><th class="num">年还款额</th><th class="num">其中本金</th>
        <th class="num">其中利息</th><th class="num">年末剩余本金</th></tr></thead>
      <tbody>${body}</tbody></table>`;
  }

  /** 结果卡片：等额本息强调「每月月供」，等额本金强调「首月/末月」 */
  function summaryHtml(res) {
    const eq = res.method === 'equal_installment';
    const cards = [];
    cards.push({ k: '贷款金额', v: FIN.money(res.principal), cls: '' });
    if (eq) {
      cards.push({ k: '每月月供', v: FIN.money(res.monthly), cls: 'main' });
      if (Math.abs(res.lastPayment - res.monthly) >= 0.01) {
        cards.push({ k: '最后一期', v: FIN.money(res.lastPayment), cls: '' });
      }
    } else {
      cards.push({ k: '首月月供', v: FIN.money(res.firstPayment), cls: 'main' });
      cards.push({ k: '末月月供', v: FIN.money(res.lastPayment), cls: '' });
    }
    cards.push({ k: '支付利息总额', v: FIN.money(res.totalInterest), cls: '' });
    cards.push({ k: '还款总额', v: FIN.money(res.totalPayment), cls: '' });
    return cards.map(c =>
      `<div class="res ${c.cls}"><div class="k">${esc(c.k)}</div><div class="v">${esc(c.v)}</div></div>`
    ).join('');
  }

  /** 按年汇总表：金钱的时间分布一眼能看出来（前期利息多、后期本金多） */
  function yearTableHtml(res) {
    const rows = res.byYear.map(y => `<tr>
        <td>第 ${y.year} 年</td>
        <td class="num">${FIN.money(y.payment)}</td>
        <td class="num">${FIN.money(y.principal)}</td>
        <td class="num">${FIN.money(y.interest)}</td>
        <td class="num">${FIN.money(y.balance)}</td>
      </tr>`).join('');
    return `<table>
      <thead><tr><th>时间</th><th class="num">年还款额</th><th class="num">其中本金</th>
        <th class="num">其中利息</th><th class="num">年末剩余本金</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  /** 每期明细（默认折叠，期数多时不必展开） */
  function scheduleTableHtml(res, unit) {
    const label = unit || '期';
    const rows = res.schedule.map(s => `<tr>
        <td>第 ${s.period} ${label}</td>
        <td class="num">${FIN.money(s.payment)}</td>
        <td class="num">${FIN.money(s.principal)}</td>
        <td class="num">${FIN.money(s.interest)}</td>
        <td class="num">${FIN.money(s.balance)}</td>
      </tr>`).join('');
    return `<table>
      <thead><tr><th>期数</th><th class="num">月供</th><th class="num">本金</th>
        <th class="num">利息</th><th class="num">剩余本金</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  return { esc, cardsHtml, summaryHtml, yearTableHtml, yearRowsMerged, yearTableRowsHtml, scheduleTableHtml };
})();
