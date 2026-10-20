# -*- coding: utf-8 -*-
"""小红书视频转笔记：链接 → 下载 → 转写 → 总结 → markdown。

模块职责：
    resolve    链接解析（短链跟跳转、图文正文兜底）
    download   yt-dlp 封装
    audio      ffmpeg 抽音轨与切片
    asr        云端语音转写
    summarize  大模型总结
    store      文件名清洗与 markdown 组装
    jobs       作业状态与事件总线
    pipeline   把上面几块串成一条流水线
    config     配置读写
"""

__version__ = "1.0.0"
