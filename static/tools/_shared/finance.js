/* 工具箱共用的金融计算库。
 *
 * 这里只放「纯函数」：不碰 DOM、不读全局状态，所以既能被页面用，也能在 node 里
 * 直接抽出来跑测试用例（tools/check_finance.js 会做这件事）—— 算钱的代码必须能验证。
 *
 * 约定：比例一律用「百分数」传入（8 表示 8%），金额返回保留两位小数的数字，
 *      利率用百分数（4.9 表示 4.9%），期限用「月」。
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;  // node
  root.FIN = api;                                                          // 浏览器
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // 工资薪金个税预扣率表（累计预扣法，同时也是年度综合所得税率表）
  const IIT_BRACKETS = [
    { limit: 36000,    rate: 0.03, quick: 0 },
    { limit: 144000,   rate: 0.10, quick: 2520 },
    { limit: 300000,   rate: 0.20, quick: 16920 },
    { limit: 420000,   rate: 0.25, quick: 31920 },
    { limit: 660000,   rate: 0.30, quick: 52920 },
    { limit: 960000,   rate: 0.35, quick: 85920 },
    { limit: Infinity, rate: 0.45, quick: 181920 },
  ];

  // 全年一次性奖金「单独计税」用的按月换算后的综合所得税率表
  const BONUS_BRACKETS = [
    { limit: 3000,     rate: 0.03, quick: 0 },
    { limit: 12000,    rate: 0.10, quick: 210 },
    { limit: 25000,    rate: 0.20, quick: 1410 },
    { limit: 35000,    rate: 0.25, quick: 2660 },
    { limit: 55000,    rate: 0.30, quick: 4410 },
    { limit: 80000,    rate: 0.35, quick: 7160 },
    { limit: Infinity, rate: 0.45, quick: 15160 },
  ];

  function toNum(v, d) {
    const n = Number(v);
    return Number.isFinite(n) ? n : (d || 0);
  }

  /** 四舍五入到分。用 EPSILON 抵消浮点误差（例如 1.005 → 1.01） */
  function round2(x) {
    return Math.round((toNum(x) + Number.EPSILON) * 100) / 100;
  }

  /** 千分位金额，默认两位小数 */
  function money(x, digits) {
    const d = digits == null ? 2 : digits;
    return toNum(x).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
  }

  /** 缴费基数按上下限封顶/保底（政策要求：低于下限按下限，高于上限按上限） */
  function clampBase(value, min, max) {
    const v = toNum(value);
    const lo = min == null || min === '' ? null : toNum(min);
    const hi = max == null || max === '' ? null : toNum(max);
    if (lo != null && v < lo) return { value: lo, clamped: 'min' };
    if (hi != null && v > hi) return { value: hi, clamped: 'max' };
    return { value: v, clamped: null };
  }

  function pickBracket(table, amount) {
    const t = Math.max(0, toNum(amount));
    for (const b of table) if (t <= b.limit) return b;
    return table[table.length - 1];
  }

  /** 查累计应纳税所得额对应的预扣率与速算扣除数 */
  function iitRate(taxableCum) {
    return pickBracket(IIT_BRACKETS, taxableCum);
  }

  /** 按年度综合所得税率表算税额（用于年终奖「并入」的对比） */
  function annualTax(taxableAnnual) {
    const b = pickBracket(IIT_BRACKETS, taxableAnnual);
    return Math.max(0, round2(Math.max(0, toNum(taxableAnnual)) * b.rate - b.quick));
  }

  /**
   * 工资个税：按累计预扣法逐月计算。
   * 累计应纳税所得额 = 累计收入 − 累计(起征点 + 个人五险一金 + 专项附加扣除)
   * 当月税额 = (累计应纳税所得额 × 预扣率 − 速算扣除数) − 累计已缴
   */
  function iitSchedule(o) {
    const baseMonths = Math.max(1, Math.round(toNum(o.months, 12)));
    const extraMonths = Math.max(0, Math.round(toNum(o.extraSalaryMonths, 0)));
    const totalMonths = baseMonths + extraMonths;
    const salary = toNum(o.monthlyIncome);
    const social = toNum(o.socialInsurance);
    const special = toNum(o.specialDeduction);
    const threshold = o.threshold == null ? 5000 : toNum(o.threshold, 5000);

    const rows = [];
    let taxPaid = 0;
    for (let m = 1; m <= totalMonths; m++) {
      // 13/14 薪是额外的一笔工资：计入累计收入，但不重复享受每月 5000 起征点、
      // 五险一金和专项附加扣除（这些一年只有 12 个月）
      const extra = m > baseMonths;
      const incomeCum = salary * m;
      const deductionMonths = Math.min(m, baseMonths);
      const taxableCum = Math.max(0, round2(incomeCum - (threshold + social + special) * deductionMonths));
      const b = iitRate(taxableCum);
      const taxDueCum = Math.max(0, round2(taxableCum * b.rate - b.quick));
      const tax = Math.max(0, round2(taxDueCum - taxPaid));
      taxPaid = round2(taxPaid + tax);
      rows.push({
        month: m, extra, incomeCum: round2(incomeCum), taxableCum,
        rate: b.rate, quick: b.quick, taxDueCum, tax, taxPaid,
        net: round2(salary - (extra ? 0 : social) - tax),
      });
    }
    const yearNet = round2(rows.reduce((s, r) => s + r.net, 0));
    return {
      rows,
      yearTax: taxPaid,
      yearNet,
      // 月均到手按 12 个正常发薪月摊，13/14 薪不会把月均数拉低
      monthlyNetAvg: round2(yearNet / baseMonths),
      baseMonths, extraMonths, totalMonths,
    };
  }

  /** 年终奖「单独计税」的税额（只取决于奖金本身，便于做临界点提示） */
  function bonusSeparateTax(bonus) {
    const b = pickBracket(BONUS_BRACKETS, toNum(bonus) / 12);
    return Math.max(0, round2(toNum(bonus) * b.rate - b.quick));
  }

  /**
   * 年终奖两种计税方式对比。
   *   单独计税：奖金 ÷ 12 查「按月换算表」→ 税 = 奖金 × 税率 − 速算扣除数
   *   并入综合所得：全年（工资 + 奖金 − 各项扣除）按年度税率表算，减去工资部分本来要缴的税
   * 政策依据：财税〔2018〕164号，并已延续至 2027-12-31。
   */
  function yearlyBonusTax(o) {
    const bonus = Math.max(0, toNum(o.bonus));
    const annualIncome = Math.max(0, toNum(o.annualIncome));
    const annualDeductions = Math.max(0, toNum(o.annualDeductions));  // 60000 + 社保公积金 + 专项附加

    const separateBracket = pickBracket(BONUS_BRACKETS, bonus / 12);
    const separateTax = bonusSeparateTax(bonus);

    const salaryOnlyTax = annualTax(annualIncome - annualDeductions);
    const combinedTotalTax = annualTax(annualIncome + bonus - annualDeductions);
    const combinedTax = Math.max(0, round2(combinedTotalTax - salaryOnlyTax));

    const better = separateTax <= combinedTax ? 'separate' : 'combined';
    return {
      bonus, separateTax, combinedTax, salaryOnlyTax, combinedTotalTax,
      separateBracket, better,
      saving: round2(Math.abs(separateTax - combinedTax)),
      separateNet: round2(bonus - separateTax),
      combinedNet: round2(bonus - combinedTax),
    };
  }

  /**
   * 五险一金：按各自缴费基数与比例算个人 / 单位金额。
   * 工伤、生育、大病医疗互助一般只有单位缴；公积金基数可与社保基数不同。
   */
  function socialInsurance(o) {
    const base = toNum(o.base);
    const fundBase = (o.fundBase == null || o.fundBase === '') ? base : toNum(o.fundBase);
    const e = o.employee || {};
    const r = o.employer || {};
    const defs = [
      { key: 'pension',      name: '养老保险',           eBase: base,     rBase: base,     e: e.pension,      r: r.pension },
      { key: 'medical',      name: '医疗保险',           eBase: base,     rBase: base,     e: e.medical,      r: r.medical },
      { key: 'unemployment', name: '失业保险',           eBase: base,     rBase: base,     e: e.unemployment, r: r.unemployment },
      { key: 'injury',       name: '工伤保险',           eBase: 0,        rBase: base,     e: 0,              r: r.injury },
      { key: 'maternity',    name: '生育保险',           eBase: 0,        rBase: base,     e: 0,              r: r.maternity },
      { key: 'serious',      name: '大病医疗互助',       eBase: 0,        rBase: base,     e: 0,              r: r.serious },
      { key: 'fund',         name: '住房公积金',         eBase: fundBase, rBase: fundBase, e: e.fund,         r: r.fund },
    ];
    const rows = defs.map(d => ({
      key: d.key,
      name: d.name,
      employeeRate: toNum(d.e),
      employerRate: toNum(d.r),
      employee: round2(d.eBase * toNum(d.e) / 100),
      employer: round2(d.rBase * toNum(d.r) / 100),
    }));
    return {
      rows,
      employeeTotal: round2(rows.reduce((s, x) => s + x.employee, 0)),
      employerTotal: round2(rows.reduce((s, x) => s + x.employer, 0)),
    };
  }

  /** 单笔贷款的摊还表：等额本息 / 等额本金。末期用剩余本金兜底，避免尾差 */
  function amortize(o) {
    const principal = toNum(o.principal);
    const months = Math.max(1, Math.round(toNum(o.months, 12)));
    const rate = toNum(o.annualRate) / 100 / 12;
    const method = o.method === 'equal_principal' ? 'equal_principal' : 'equal_installment';

    const schedule = [];
    let totalInterest = 0;
    let totalPayment = 0;
    let monthly = 0;

    if (method === 'equal_installment') {
      const pow = Math.pow(1 + rate, months);
      monthly = round2(rate === 0 ? principal / months : principal * rate * pow / (pow - 1));
      let balance = principal;
      for (let i = 1; i <= months; i++) {
        const interest = round2(balance * rate);
        let principalPart = round2(monthly - interest);
        let pay = monthly;
        if (i === months) {
          principalPart = round2(balance);
          pay = round2(principalPart + interest);
        }
        balance = round2(balance - principalPart);
        totalInterest = round2(totalInterest + interest);
        totalPayment = round2(totalPayment + pay);
        schedule.push({ period: i, payment: pay, principal: principalPart, interest, balance });
      }
    } else {
      const evenPrincipal = round2(principal / months);
      let balance = principal;
      for (let i = 1; i <= months; i++) {
        const interest = round2(balance * rate);
        const principalPart = (i === months) ? round2(balance) : evenPrincipal;
        const pay = round2(principalPart + interest);
        balance = round2(balance - principalPart);
        totalInterest = round2(totalInterest + interest);
        totalPayment = round2(totalPayment + pay);
        schedule.push({ period: i, payment: pay, principal: principalPart, interest, balance });
      }
    }

    const byYear = [];
    for (let i = 0; i < schedule.length; i += 12) {
      const chunk = schedule.slice(i, i + 12);
      byYear.push({
        year: Math.floor(i / 12) + 1,
        payment: round2(chunk.reduce((s, x) => s + x.payment, 0)),
        principal: round2(chunk.reduce((s, x) => s + x.principal, 0)),
        interest: round2(chunk.reduce((s, x) => s + x.interest, 0)),
        balance: chunk[chunk.length - 1].balance,
      });
    }

    return {
      method, months, principal, monthly,
      firstPayment: schedule.length ? schedule[0].payment : 0,
      lastPayment: schedule.length ? schedule[schedule.length - 1].payment : 0,
      totalInterest, totalPayment, schedule, byYear,
    };
  }

  /** 组合贷：把两笔（公积金 + 商业）贷款合起来看 */
  function combinedLoan(parts) {
    const loans = (parts || []).map(p => amortize(p));
    const totalPrincipal = round2(loans.reduce((s, l) => s + toNum(l.principal), 0));
    const totalInterest = round2(loans.reduce((s, l) => s + toNum(l.totalInterest), 0));
    const firstMonth = round2(loans.reduce((s, l) => s + toNum(l.firstPayment), 0));
    const monthlySum = round2(loans.reduce(
      (s, l) => s + (l.method === 'equal_installment' ? toNum(l.monthly) : toNum(l.firstPayment)), 0));
    return {
      loans, totalPrincipal, totalInterest,
      totalPayment: round2(totalPrincipal + totalInterest),
      monthlySum, firstMonth,
    };
  }

  /** 按给定余额、月利率、期数生成摊还表（提前还款后继续还的场景） */
  function buildSchedule(o) {
    const balance0 = toNum(o.balance);
    const rate = toNum(o.monthlyRate);
    const months = Math.max(1, Math.round(toNum(o.months, 1)));
    const method = o.method === 'equal_principal' ? 'equal_principal' : 'equal_installment';
    const schedule = [];
    let totalInterest = 0, totalPayment = 0, monthly = 0;

    if (method === 'equal_installment') {
      const pow = Math.pow(1 + rate, months);
      const fixed = o.monthlyPayment != null && o.monthlyPayment !== ''
        ? toNum(o.monthlyPayment)
        : (rate === 0 ? balance0 / months : balance0 * rate * pow / (pow - 1));
      monthly = round2(fixed);
      let balance = balance0;
      for (let i = 1; i <= months; i++) {
        const interest = round2(balance * rate);
        let principalPart = round2(monthly - interest);
        let pay = monthly;
        if (i === months || principalPart >= balance) {
          principalPart = round2(balance);
          pay = round2(principalPart + interest);
        }
        balance = round2(balance - principalPart);
        totalInterest = round2(totalInterest + interest);
        totalPayment = round2(totalPayment + pay);
        schedule.push({ period: i, payment: pay, principal: principalPart, interest, balance });
      }
    } else {
      const evenPrincipal = round2(balance0 / months);
      let balance = balance0;
      for (let i = 1; i <= months; i++) {
        const interest = round2(balance * rate);
        const principalPart = (i === months || evenPrincipal >= balance) ? round2(balance) : evenPrincipal;
        const pay = round2(principalPart + interest);
        balance = round2(balance - principalPart);
        totalInterest = round2(totalInterest + interest);
        totalPayment = round2(totalPayment + pay);
        schedule.push({ period: i, payment: pay, principal: principalPart, interest, balance });
      }
    }
    return { method, months, monthly, schedule, totalInterest, totalPayment, byYear: byYearOf(schedule),
             firstPayment: schedule.length ? schedule[0].payment : 0,
             lastPayment: schedule.length ? schedule[schedule.length - 1].payment : 0 };
  }

  /** 把逐期明细按 12 期一组汇总成年度数据 */
  function byYearOf(schedule) {
    const out = [];
    for (let i = 0; i < schedule.length; i += 12) {
      const chunk = schedule.slice(i, i + 12);
      out.push({
        year: Math.floor(i / 12) + 1,
        payment: round2(chunk.reduce((s, x) => s + x.payment, 0)),
        principal: round2(chunk.reduce((s, x) => s + x.principal, 0)),
        interest: round2(chunk.reduce((s, x) => s + x.interest, 0)),
        balance: chunk[chunk.length - 1].balance,
      });
    }
    return out;
  }

  /** 等额本息下，给定月供与余额，反解还需多少期 */
  function remainingPeriods(balance, rate, payment) {
    const b = toNum(balance), p = toNum(payment);
    if (b <= 0) return 0;
    if (p <= 0) return Infinity;
    if (toNum(rate) === 0) return Math.ceil(b / p);
    const x = 1 - b * rate / p;
    if (x <= 0) return Infinity;                 // 月供连利息都不够
    return Math.ceil(-Math.log(x) / Math.log(1 + rate));
  }

  /**
   * 提前还款测算。
   *   strategy = 'reduce_payment'（减少月供、期限不变）
   *            = 'shorten_term'  （缩短期限、月供不变）
   * 返回原计划与新计划的总利息、节省金额、新月供/新期数等。
   */
  function prepayAnalysis(o) {
    const principal = toNum(o.principal);
    const annualRate = toNum(o.annualRate);
    const rate = annualRate / 100 / 12;
    const months = Math.max(1, Math.round(toNum(o.months, 360)));
    const method = o.method === 'equal_principal' ? 'equal_principal' : 'equal_installment';
    const paid = Math.min(months, Math.max(0, Math.round(toNum(o.paidPeriods))));
    const prepay = Math.max(0, toNum(o.prepayAmount));
    const strategy = o.strategy === 'shorten_term' ? 'shorten_term' : 'reduce_payment';

    const original = amortize({ principal, annualRate, months, method });
    const paidRows = original.schedule.slice(0, paid);
    const paidInterest = round2(paidRows.reduce((s, x) => s + x.interest, 0));
    const paidPayment = round2(paidRows.reduce((s, x) => s + x.payment, 0));
    const balanceBefore = paid < months ? original.schedule[paid - 1].balance : 0;
    const remainMonths = months - paid;
    const oldRows = original.schedule.slice(paid);
    const oldRemainingInterest = round2(oldRows.reduce((s, x) => s + x.interest, 0));
    const oldRemainingPayment = round2(oldRows.reduce((s, x) => s + x.payment, 0));
    const oldMonthly = method === 'equal_installment'
      ? original.monthly
      : (oldRows.length ? oldRows[0].payment : 0);

    const balanceAfter = round2(Math.max(0, balanceBefore - prepay));
    let next;
    if (balanceAfter <= 0) {
      next = { months: 0, monthly: 0, schedule: [], totalInterest: 0, totalPayment: 0,
               firstPayment: 0, lastPayment: 0 };
    } else if (strategy === 'reduce_payment') {
      next = buildSchedule({ balance: balanceAfter, monthlyRate: rate, months: remainMonths, method });
    } else if (method === 'equal_installment') {
      const periods = remainingPeriods(balanceAfter, rate, oldMonthly);
      next = buildSchedule({ balance: balanceAfter, monthlyRate: rate, months: periods,
                             method, monthlyPayment: oldMonthly });
    } else {
      // 等额本金「缩短期限」＝ 每月还的本金不变，自然提前还完
      const evenPrincipal = round2(principal / months);
      const periods = Math.max(1, Math.ceil(balanceAfter / evenPrincipal));
      next = buildSchedule({ balance: balanceAfter, monthlyRate: rate, months: periods, method });
    }

    const savedInterest = round2(oldRemainingInterest - next.totalInterest);
    const oldTotalOutlay = round2(paidPayment + oldRemainingPayment);
    const newTotalOutlay = round2(paidPayment + prepay + next.totalPayment);

    return {
      method, strategy, principal, months, paid, prepay, annualRate,
      balanceBefore, balanceAfter,
      paidInterest, paidPayment,
      oldMonthly, oldRemainingMonths: remainMonths, oldRemainingInterest, oldRemainingPayment,
      oldTotalOutlay, oldTotalInterest: original.totalInterest,
      newMonthly: next.monthly || next.firstPayment,
      newFirstPayment: next.firstPayment,
      newRemainingMonths: next.months,
      newRemainingInterest: next.totalInterest,
      newRemainingPayment: next.totalPayment,
      newTotalOutlay, newTotalInterest: round2(paidInterest + next.totalInterest),
      savedInterest, savedMonths: remainMonths - next.months,
      newSchedule: next.schedule, newByYear: next.byYear || [],
    };
  }

  /**
   * 真实年化利率（IRR）：把「拿到手的本金」和「每期还款」放在同一条现金流上求解。
   * 用来识破车贷里的「手续费 / 服务费 / 前置利息」——名义利率不变，实际年化会明显上升。
   * 返回值与库内其它利率一样用「百分数」：nominalAnnual = 7.9 表示 7.9%。
   */
  function irr(o) {
    const amount = toNum(o.amount);
    const fee = toNum(o.upfrontFee);
    const payment = toNum(o.payment);
    const months = Math.max(1, Math.round(toNum(o.months, 12)));
    const net = amount - fee;

    const pv = (i) => i === 0
      ? payment * months
      : payment * (1 - Math.pow(1 + i, -months)) / i;

    if (net <= 0 || payment <= 0) {
      return { monthlyRatePercent: 0, nominalAnnual: 0, effectiveAnnual: 0, totalPayment: 0, ok: false };
    }
    if (pv(0) < net) {
      // 连零利率都收不回本金（还款总额小于本金），数据有问题
      return { monthlyRatePercent: 0, nominalAnnual: 0, effectiveAnnual: 0,
               totalPayment: round2(payment * months), ok: false };
    }
    let lo = 0, hi = 1;   // 月利率 0% ~ 100%
    for (let k = 0; k < 200; k++) {
      const mid = (lo + hi) / 2;
      if (pv(mid) > net) lo = mid; else hi = mid;   // 现值随利率递减
    }
    const i = (lo + hi) / 2;
    return {
      monthlyRatePercent: i * 100,
      nominalAnnual: i * 12 * 100,                          // 名义年化（月利率×12）
      effectiveAnnual: (Math.pow(1 + i, 12) - 1) * 100,    // 有效年化（按月复利）
      totalPayment: round2(payment * months),
      totalCost: round2(payment * months + fee - amount),
      ok: true,
    };
  }

  return {
    IIT_BRACKETS, BONUS_BRACKETS, toNum, round2, money, clampBase,
    iitRate, annualTax, iitSchedule, yearlyBonusTax, bonusSeparateTax, socialInsurance,
    amortize, combinedLoan, buildSchedule, remainingPeriods, prepayAnalysis, irr,
  };
});
