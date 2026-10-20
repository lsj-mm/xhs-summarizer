/* 跟读匹配的回归测试：node tools/check_readback.js */
'use strict';

const path = require('path');
const RB = require(path.join(__dirname, '..', 'static', 'tools', '_shared', 'readback.js'));

let failed = 0;
function check(ok, label, detail) {
  if (!ok) failed++;
  console.log((ok ? '  ✓ ' : '  ✗ ') + label + (detail ? '  [' + detail + ']' : ''));
}

console.log('=== 跟读匹配 ===');
const exact = RB.compare('PUT is idempotent.', 'put is idempotent');
check(exact.score === 100 && exact.matched === 3, '大小写/标点不影响，完全匹配 100%', exact.score + '%');
check(exact.target.every((item) => item.matched), '目标词全部匹配');

const missing = RB.compare('Please retry the request.', 'Please retry request');
check(missing.score === 75 && missing.matched === 3 && missing.missing.includes('the'),
  '漏掉 the 时为 75%，并列出漏词', missing.score + '% / ' + missing.missing.join(','));

const extra = RB.compare('Hello world', 'Hello there world');
check(extra.score === 100 && extra.extra.includes('there'),
  '多识别 there 不扣目标匹配度，但会单独列出', extra.score + '% / ' + extra.extra.join(','));

const wrong = RB.compare('I want to borrow your book.', 'I want to lend your book.');
check(wrong.score < 100 && wrong.missing.includes('borrow') && wrong.extra.includes('lend'),
  '用错词时能指出目标词没被识别、识别出了另一个词', wrong.score + '%');

const reorder = RB.compare('a b c', 'a c b');
check(reorder.score === 67 && reorder.matched === 2, '词序变化按最长公共子序列计算', reorder.score + '%');

const empty = RB.compare('Hello world', '');
check(empty.score === 0 && empty.missing.length === 2, '没有识别结果时分数为 0', empty.score + '%');

console.log('\n' + (failed ? `  ${failed} 项失败` : '  全部用例通过'));
process.exit(failed ? 1 : 0);
