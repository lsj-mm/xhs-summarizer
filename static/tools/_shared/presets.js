/* 政策参数预设：目前只放成都 / 四川，其它城市可以照着加。
 *
 * ⚠️ 政策每年调整，这里的数字是「某个年度」的快照，界面上都可以改。
 * 数据来源与年度写在 source / version 里，改的时候请一起更新。
 */
window.POLICY_PRESETS = {
  version: '2026 年度',
  updatedAt: '2026-10',

  cities: {
    chengdu: {
      name: '成都 / 四川（2026 年度）',
      social: {
        // 四川省人社厅：2026 年度三项社会保险缴费基数上限 23493、下限 4699 元/月
        baseMin: 4699,
        baseMax: 23493,
        averageWage: 7831,
        employee: { pension: 8, medical: 2, unemployment: 0.4, fund: 6 },
        employer: { pension: 16, medical: 6.75, unemployment: 0.6,
                    injury: 0.2, maternity: 0.8, serious: 0.75, fund: 6 },
      },
      fund: {
        // 成都住房公积金管委会：2026 年度缴存基数上限 32969 元/月；
        // 下限随最低工资标准，中心城区 2330、郊区 2200
        baseMin: 2330,
        baseMinSuburb: 2200,
        baseMax: 32969,
        rateMin: 5,
        rateMax: 12,
      },
      notes: [
        '社保基数上下限：四川省人社厅 2026 年度通知（上限 23493、下限 4699 元/月）',
        '医保基数与社保一致（上限 23493、下限 4699）',
        '医保单位费率 = 基本医疗 6.75% + 生育 0.8% + 大病医疗互助 0.75%',
        '工伤保险按行业分八类（0.2%~1.9%），这里取最低的一类',
        '公积金比例 5%~12%，单位与个人同比例；下限中心城区 2330、郊区 2200',
      ],
    },
  },

  // 房贷 / 车贷的参考利率（现实中会变，界面上可改）
  rates: {
    mortgageCommercial: 3.1,   // 商业房贷参考
    mortgageFund: 2.85,        // 公积金房贷（5 年以上）参考
    carLoan: 4.5,              // 车贷参考
  },

  sources: [
    { label: '四川省人社厅：2026 年度社会保险缴费基数上下限（上限 23493、下限 4699）',
      url: 'https://rst.sc.gov.cn/rst/zdmsxxzccs/2026/9/7/59ef71878a0c49f3b477d110ac35df6a.shtml' },
    { label: '四川省政府：四川 2026 年度社保缴费基数公布',
      url: 'https://www.sc.gov.cn/10462/10464/10797/2026/9/9/4f59ccad3d554915a589d1fc2b224e19.shtml' },
    { label: '成都职工医保 2026 年度缴费基数与比例',
      url: 'https://m.sohu.com/a/1077887610_498271/' },
    { label: '成都住房公积金管委会：调整缴存基数通知（上限 32969 元）',
      url: 'https://cdzfgjj.chengdu.gov.cn/gkml/qtwj/1534506168435605504.shtml' },
    { label: '财政部 税务总局：全年一次性奖金政策延续至 2027-12-31',
      url: 'https://chongqing.chinatax.gov.cn/cqtax/zt/fzjyjd/zcfg/fgft/202308/t20230828_355353.html' },
  ],
};
