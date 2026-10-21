/* 金额计算库的回归测试（用 node 跑，不需要浏览器）。
 *
 *   node tools/check_finance.js
 *
 * 覆盖：等额本息 / 等额本金、个税累计预扣法、五险一金、金额取整与格式化。
 * 多数用例来自公开的房贷计算示例，可以对上外部计算结果。
 */
'use strict';
const fs = require('fs');
const path = require('path');

const FIN = require(path.join(__dirname, '..', 'static', 'tools', '_shared', 'finance.js'));

let failed = 0;
function check(ok, label, detail) {
  if (!ok) failed++;
  console.log((ok ? '  ✓ ' : '  ✗ ') + label + (detail ? '  [' + detail + ']' : ''));
}
function near(a, b, tol) {
  return Math.abs(a - b) <= (tol == null ? 0.01 : tol);
}

console.log('=== 金额取整与格式化 ===');
check(FIN.round2(1.005) === 1.01, 'round2(1.005) 应为 1.01', String(FIN.round2(1.005)));
check(FIN.round2(2.675) === 2.68, 'round2(2.675) 应为 2.68', String(FIN.round2(2.675)));
check(FIN.money(1234567.891) === '1,234,567.89', '千分位格式化', FIN.money(1234567.891));
check(FIN.money(1000, 0) === '1,000', '整数格式化', FIN.money(1000, 0));

console.log('\n=== 等额本息 ===');
// 经典示例：100 万 / 30 年 / 4.9%，月供约 5307.27 元
const eq = FIN.amortize({ principal: 1000000, annualRate: 4.9, months: 360, method: 'equal_installment' });
check(near(eq.monthly, 5307.27, 1), '100万/30年/4.9% 月供约 5307.27', eq.monthly.toFixed(2));
check(near(eq.totalInterest, 910617, 60), '利息总额约 91 万', eq.totalInterest.toFixed(2));
const principalSum = FIN.round2(eq.schedule.reduce((s, x) => s + x.principal, 0));
check(principalSum === 1000000, '各期本金之和正好等于贷款额（无尾差）', principalSum.toFixed(2));
check(near(eq.totalPayment, FIN.round2(1000000 + eq.totalInterest)), '还款总额 = 本金 + 利息',
  eq.totalPayment.toFixed(2));
check(eq.schedule[0].balance === FIN.round2(1000000 - eq.schedule[0].principal), '首期后余额计算正确');
check(eq.schedule[359].balance === 0, '末期余额归零', String(eq.schedule[359].balance));

const eq2 = FIN.amortize({ principal: 1000000, annualRate: 3.1, months: 360, method: 'equal_installment' });
check(eq2.monthly > 4265 && eq2.monthly < 4275, '100万/30年/3.1% 月供落在 4270 附近', eq2.monthly.toFixed(2));
const eq0 = FIN.amortize({ principal: 120000, annualRate: 0, months: 12, method: 'equal_installment' });
check(eq0.monthly === 10000 && eq0.totalInterest === 0, '零利率时退化为等额本金且无利息',
  eq0.monthly.toFixed(2));

console.log('\n=== 等额本金 ===');
const ep = FIN.amortize({ principal: 1000000, annualRate: 4.9, months: 360, method: 'equal_principal' });
check(near(ep.firstPayment, 6861.11, 0.5), '首月月供 ≈ 6861.11', ep.firstPayment.toFixed(2));
// 末期本金不是 100万/360=2777.78 那个整数：2777.78×360 会比贷款额多 0.8 元，
// 所以末期用剩余余额兜底（银行也是这么处理的），月供因此略低一点
check(near(ep.schedule[358].balance, 2776.98, 1), '倒数第二期末余额吸收取整差',
  ep.schedule[358].balance.toFixed(2));
check(near(ep.lastPayment, 2788.32, 0.5), '末月月供 ≈ 2788.32（含末期取整收敛）', ep.lastPayment.toFixed(2));
check(near(ep.totalInterest, 737041.67, 1), '利息总额 ≈ 737041.67', ep.totalInterest.toFixed(2));
check(ep.firstPayment > ep.lastPayment, '月供逐月递减');
const epSum = FIN.round2(ep.schedule.reduce((s, x) => s + x.principal, 0));
check(epSum === 1000000, '各期本金之和正好等于贷款额', epSum.toFixed(2));

console.log('\n=== 个税（累计预扣法） ===');
// 月薪 30000，个人五险一金 5000，专项附加扣除 2000，起征点 5000
// 每月应纳税所得额 = 30000 − 5000 − 5000 − 2000 = 18000，累计额决定档位
const iit = FIN.iitSchedule({ monthlyIncome: 30000, socialInsurance: 5000, specialDeduction: 2000, threshold: 5000, months: 12 });
check(iit.rows[0].taxableCum === 18000, '1 月累计应纳税所得额 18000', String(iit.rows[0].taxableCum));
check(iit.rows[0].tax === 540, '1 月个税 540（3% 档）', String(iit.rows[0].tax));
// 3% 档上限是「累计 36000」，第 2 个月末刚好用满，所以第 3 个月跳 10% 档
check(iit.rows[2].tax === 1800, '3 月跳 10% 档，当月个税 1800', String(iit.rows[2].tax));
// 20% 档起点是累计 144000，第 8 个月末达到，所以第 9 个月跳档
check(iit.rows[8].tax === 3600, '9 月跳 20% 档，当月个税 3600', String(iit.rows[8].tax));
check(iit.rows[11].tax === 3600, '12 月个税 3600', String(iit.rows[11].tax));
check(iit.yearTax === 26280, '全年个税 26280', String(iit.yearTax));
check(iit.rows[11].net === 21400, '12 月到手 21400', String(iit.rows[11].net));
check(near(iit.yearNet, iit.rows.reduce((s, r) => s + r.net, 0)), '全年到手 = 各月到手之和');
check(FIN.iitRate(36000).rate === 0.03 && FIN.iitRate(36000.01).rate === 0.10, '档位边界按「不超过」判定');
check(FIN.iitRate(1000000).rate === 0.45 && FIN.iitRate(1000000).quick === 181920, '最高档 45% / 速算 181920');

console.log('\n=== 13/14 薪 ===');
const iit13 = FIN.iitSchedule({ monthlyIncome: 30000, socialInsurance: 5000, specialDeduction: 2000,
  threshold: 5000, months: 12, extraSalaryMonths: 1 });
check(iit13.rows.length === 13, '13 薪生成 13 行', String(iit13.rows.length));
check(iit13.rows[12].extra === true, '第 13 行标记为额外薪');
check(near(iit13.rows[12].incomeCum, iit.rows[11].incomeCum + 30000), '13 薪全额计入累计收入',
  String(iit13.rows[12].incomeCum));
check(near(iit13.rows[12].taxableCum, iit.rows[11].taxableCum + 30000),
  '13 薪不再重复扣五险一金和起征点', String(iit13.rows[12].taxableCum));
check(iit13.rows[12].net === FIN.round2(30000 - iit13.rows[12].tax),
  '13 薪到手 = 月薪 − 个税（不再扣五险一金）', String(iit13.rows[12].net));
check(iit13.yearTax > iit.yearTax, '13 薪比 12 薪多缴税', `${iit.yearTax} → ${iit13.yearTax}`);
const iit14 = FIN.iitSchedule({ monthlyIncome: 30000, socialInsurance: 5000, specialDeduction: 2000,
  threshold: 5000, months: 12, extraSalaryMonths: 2 });
check(iit14.rows.length === 14 && iit14.rows[13].extra === true, '14 薪生成 14 行');
check(iit14.yearNet > iit13.yearNet, '14 薪全年到手更高', `${iit13.yearNet} → ${iit14.yearNet}`);
const iit17 = FIN.iitSchedule({ monthlyIncome: 30000, socialInsurance: 5000, specialDeduction: 2000,
  threshold: 5000, months: 12, extraSalaryMonths: 5 });
check(iit17.rows.length === 17 && iit17.rows[16].extra === true, '17 薪生成 17 行');
check(near(iit17.rows[16].incomeCum, iit.rows[11].incomeCum + 5 * 30000),
  '17 薪累计收入 = 12 个月工资 + 5 个月', String(iit17.rows[16].incomeCum));

const low = FIN.iitSchedule({ monthlyIncome: 5000, socialInsurance: 0, specialDeduction: 0, threshold: 5000, months: 12 });
check(low.yearTax === 0, '月薪 5000 且无扣除时不用缴税', String(low.yearTax));
const mid = FIN.iitSchedule({ monthlyIncome: 10000, socialInsurance: 2000, specialDeduction: 1000, threshold: 5000, months: 12 });
check(mid.yearTax === 720, '月薪 1 万 / 扣 2000+1000 时全年个税 720', String(mid.yearTax));

console.log('\n=== 五险一金 ===');
const si = FIN.socialInsurance({
  base: 10000, fundBase: '',
  employee: { pension: 8, medical: 2, unemployment: 0.5, fund: 7 },
  employer: { pension: 16, medical: 9.5, unemployment: 0.5, injury: 0.2, maternity: 0.8, fund: 7 },
});
check(si.employeeTotal === 1750, '个人合计 1750（800+200+50+700）', String(si.employeeTotal));
check(si.employerTotal === 3400, '单位合计 3400', String(si.employerTotal));
const injury = si.rows.find(r => r.key === 'injury');
check(injury.employee === 0 && injury.employer === 20, '工伤只有单位缴（10000×0.2%=20）');
const fund = si.rows.find(r => r.key === 'fund');
check(fund.employee === 700, '公积金按 7% 计 700', String(fund.employee));
const si2 = FIN.socialInsurance({ base: 10000, fundBase: 20000, employee: { fund: 7 }, employer: {} });
check(si2.rows.find(r => r.key === 'fund').employee === 1400, '公积金基数可与社保基数不同', '20000×7%=1400');
const siNoFund = FIN.socialInsurance({ base: 10000, fundBase: 0, employee: { fund: 7 }, employer: { fund: 7 } });
check(siNoFund.rows.find(r => r.key === 'fund').employee === 0, '公积金基数为 0 时不缴公积金', '0×7%=0');

console.log('\n=== 缴费基数上下限封顶 ===');
check(FIN.clampBase(3000, 4699, 23493).value === 4699
  && FIN.clampBase(3000, 4699, 23493).clamped === 'min', '低于下限按下限（成都 4699）');
check(FIN.clampBase(30000, 4699, 23493).value === 23493
  && FIN.clampBase(30000, 4699, 23493).clamped === 'max', '高于上限按上限（成都 23493）');
check(FIN.clampBase(10000, 4699, 23493).clamped === null, '区间内按实际工资');
check(FIN.clampBase(10000, null, null).value === 10000, '不设上下限时不调整');

console.log('\n=== 成都五险一金（2026 年度口径） ===');
const cd = FIN.socialInsurance({
  base: 10000, fundBase: 10000,
  employee: { pension: 8, medical: 2, unemployment: 0.4, fund: 6 },
  employer: { pension: 16, medical: 6.75, unemployment: 0.6, injury: 0.2, maternity: 0.8, serious: 0.75, fund: 6 },
});
check(cd.employeeTotal === 1640, '个人合计 1640（800+200+40+600）', String(cd.employeeTotal));
check(cd.employerTotal === 3110, '单位合计 3110（含大病互助 0.75%）', String(cd.employerTotal));
check(cd.rows.find(r => r.key === 'serious').employee === 0
  && cd.rows.find(r => r.key === 'serious').employer === 75, '大病医疗互助只由单位缴');
check(cd.rows.find(r => r.key === 'maternity').employer === 80, '生育保险 0.8% 由单位缴');

console.log('\n=== 年终奖：单独计税 vs 并入综合所得 ===');
// 年工资 24 万、年扣除 6 万、年终奖 3.6 万（正好卡在 3% 档上限）
const bonus = FIN.yearlyBonusTax({ bonus: 36000, annualIncome: 240000, annualDeductions: 60000 });
check(bonus.separateTax === 1080, '3.6 万奖金单独计税 1080 元（3% 档）', String(bonus.separateTax));
check(bonus.combinedTax === 7200, '并入综合所得则多缴 7200 元', String(bonus.combinedTax));
check(bonus.better === 'separate' && bonus.saving === 6120, '结论：单独计税更省 6120 元',
  `省 ${bonus.saving}`);
// 多发 1 元就掉档：36001÷12 > 3000 → 10% 档，税额从 1080 跳到 3390
const cliff = FIN.yearlyBonusTax({ bonus: 36001, annualIncome: 240000, annualDeductions: 60000 });
check(Math.abs(cliff.separateTax - 3390.1) < 0.05, '3.6001 万奖金掉到 10% 档，税 3390.1',
  cliff.separateTax.toFixed(2));
check(cliff.separateTax - bonus.separateTax > 2300, '临界点效应：多发 1 元多缴 2300 多元',
  (cliff.separateTax - bonus.separateTax).toFixed(2));
// 年收入低于扣除额时，扣除额度没用完，奖金并进来能继续抵扣，此时并入更划算
const lowBonus = FIN.yearlyBonusTax({ bonus: 20000, annualIncome: 50000, annualDeductions: 60000 });
check(lowBonus.better === 'combined', '年收入低于扣除额时并入更省',
  `单独 ${lowBonus.separateTax} / 并入 ${lowBonus.combinedTax}`);

console.log('\n=== 提前还款测算 ===');
const prepayBase = { principal: 1000000, annualRate: 4.9, months: 360,
                     method: 'equal_installment', paidPeriods: 36, prepayAmount: 200000 };
const rp = FIN.prepayAnalysis({ ...prepayBase, strategy: 'reduce_payment' });
const st = FIN.prepayAnalysis({ ...prepayBase, strategy: 'shorten_term' });
check(rp.savedInterest > 0 && st.savedInterest > 0, '两种方式都能省利息',
  `减月供省 ${rp.savedInterest} / 缩期省 ${st.savedInterest}`);
check(st.savedInterest > rp.savedInterest, '缩短期限省的利息更多',
  `${st.savedInterest} > ${rp.savedInterest}`);
check(rp.newRemainingMonths === 324 && st.newRemainingMonths < 324,
  '减月供保持期限、缩期则提前还完', `减月供剩 ${rp.newRemainingMonths} 期 / 缩期剩 ${st.newRemainingMonths} 期`);
check(rp.newMonthly < rp.oldMonthly, '减月供方式月供下降',
  `${rp.newMonthly} < ${rp.oldMonthly}`);
check(Math.abs(st.newMonthly - st.oldMonthly) < 1, '缩期方式月供基本不变',
  `${st.newMonthly} ≈ ${st.oldMonthly}`);
check(rp.newTotalOutlay < rp.oldTotalOutlay && st.newTotalOutlay < st.oldTotalOutlay,
  '提前还款后总支出下降');
const noPrepay = FIN.prepayAnalysis({ ...prepayBase, prepayAmount: 0, strategy: 'reduce_payment' });
check(Math.abs(noPrepay.savedInterest) < 1, '不提前还款时不产生"节省"', String(noPrepay.savedInterest));
const epPrepay = FIN.prepayAnalysis({ principal: 1000000, annualRate: 4.9, months: 360,
  method: 'equal_principal', paidPeriods: 36, prepayAmount: 200000, strategy: 'shorten_term' });
check(epPrepay.savedInterest > 0 && epPrepay.newRemainingMonths < 324,
  '等额本金也能测算', `省 ${epPrepay.savedInterest}，剩 ${epPrepay.newRemainingMonths} 期`);

console.log('\n=== 车贷真实年化（IRR） ===');
const car = FIN.amortize({ principal: 100000, annualRate: 4.5, months: 36, method: 'equal_installment' });
const noFee = FIN.irr({ amount: 100000, payment: car.monthly, months: 36, upfrontFee: 0 });
check(Math.abs(noFee.nominalAnnual - 4.5) < 0.05, '无手续费时 IRR 回到合同利率 4.5%',
  noFee.nominalAnnual.toFixed(3) + '%');
const withFee = FIN.irr({ amount: 100000, payment: car.monthly, months: 36, upfrontFee: 5000 });
check(withFee.nominalAnnual > 7.4 && withFee.nominalAnnual < 8.3,
  '收 5000 手续费后名义年化升到约 7.9%', withFee.nominalAnnual.toFixed(2) + '%');
check(withFee.effectiveAnnual > withFee.nominalAnnual,
  '有效年化高于名义年化', withFee.effectiveAnnual.toFixed(2) + '%');
check(Math.abs(withFee.totalCost - (car.monthly * 36 + 5000 - 100000)) < 0.5,
  '总成本 = 还款总额 + 手续费 − 本金', String(withFee.totalCost));
const bad = FIN.irr({ amount: 100000, payment: 100, months: 36, upfrontFee: 0 });
check(bad.ok === false, '还款总额低于本金时标记为数据异常');

console.log('\n=== 组合贷 ===');
const combo = FIN.combinedLoan([
  { principal: 600000, annualRate: 2.85, months: 360, method: 'equal_installment' },
  { principal: 400000, annualRate: 3.1, months: 360, method: 'equal_installment' },
]);
check(combo.totalPrincipal === 1000000, '本金合计 100 万', String(combo.totalPrincipal));
check(Math.abs(combo.monthlySum - (combo.loans[0].monthly + combo.loans[1].monthly)) < 0.01,
  '月供合计 = 两笔月供之和', combo.monthlySum.toFixed(2));
check(Math.abs(combo.totalPayment - (combo.totalPrincipal + combo.totalInterest)) < 0.01,
  '还款总额 = 本金 + 利息');
check(combo.totalInterest < FIN.amortize({ principal: 1000000, annualRate: 3.1, months: 360 }).totalInterest,
  '组合贷比全部走商贷省利息');

console.log('\n' + '-'.repeat(56));
console.log(failed ? `  ✗ 有 ${failed} 项不通过` : '  ✓ 全部用例通过');
process.exit(failed ? 1 : 0);
