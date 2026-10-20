/* 跟读匹配：把 ASR 识别出的文字和目标句做词级对比。
 *
 * 注意：这只是「识别匹配度」，不是音素级发音评分。
 * 读得含糊时 ASR 可能识别成别的词，所以分数只能作为练习参考。
 *
 * 用法：
 *   DSH_READBACK.compare('Please retry the request.', 'please retry request')
 *   -> { score: 75, matched: 3, targetCount: 4, missing: ['the'], ... }
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.DSH_READBACK = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function words(text) {
    return String(text || '')
      .toLowerCase()
      .replace(/[’]/g, "'")
      .replace(/[^a-z0-9'\-\s]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .split(' ')
      .filter(Boolean);
  }

  function compare(target, transcript) {
    const wanted = words(target);
    const heard = words(transcript);
    const n = wanted.length;
    const m = heard.length;

    if (!n) {
      return {
        score: 0, matched: 0, targetCount: 0, heardCount: m,
        target: [], heard: heard.map((word) => ({ word, matched: false })),
        missing: [], extra: heard
      };
    }

    // 最长公共子序列：只看词的顺序，不要求连续
    const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
    for (let i = 1; i <= n; i++) {
      for (let j = 1; j <= m; j++) {
        dp[i][j] = wanted[i - 1] === heard[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }

    const targetMatched = new Array(n).fill(false);
    const heardMatched = new Array(m).fill(false);
    let i = n;
    let j = m;
    let matched = 0;
    while (i > 0 && j > 0) {
      if (wanted[i - 1] === heard[j - 1]) {
        targetMatched[i - 1] = true;
        heardMatched[j - 1] = true;
        matched++;
        i--;
        j--;
      } else if (dp[i - 1][j] >= dp[i][j - 1]) {
        i--;
      } else {
        j--;
      }
    }

    const targetTokens = wanted.map((word, index) => ({ word, matched: targetMatched[index] }));
    const heardTokens = heard.map((word, index) => ({ word, matched: heardMatched[index] }));
    return {
      score: Math.round((matched / n) * 100),
      matched,
      targetCount: n,
      heardCount: m,
      target: targetTokens,
      heard: heardTokens,
      missing: targetTokens.filter((item) => !item.matched).map((item) => item.word),
      extra: heardTokens.filter((item) => !item.matched).map((item) => item.word)
    };
  }

  return { words, compare };
});
