# -*- coding: utf-8 -*-
"""炒作概念·大类归并 concept_buckets.classify 单测。

重点锁「子串误命中护栏」: 含桶关键词却语义不属该桶的概念不能被吸进桶。
"""
from backend.services import concept_buckets as cb


def test_real_robot_concepts_map_to_robot_bucket():
    # 硬件机器人相关概念应正确归入「机器人」桶
    assert cb.classify("人形机器人") == "机器人"
    assert cb.classify("工业机器人") == "机器人"
    assert cb.classify("服务机器人") == "机器人"
    assert cb.classify("减速器") == "机器人"
    assert cb.classify("丝杠") == "机器人"


def test_virtual_robot_not_robot_bucket():
    """回归: "虚拟机器人"(AI 软件/智能投顾概念)含"机器人"子串, 但不属硬件机器人板块。

    曾致 300033 同花顺买入卡误标「所属板块: 机器人·高潮」, 蹭人形机器人板块涨停热度。
    """
    assert cb.classify("虚拟机器人") == cb.OTHER


def test_other_buckets_still_work():
    assert cb.classify("高速覆铜板") == "PCB·覆铜板"
    assert cb.classify("存储芯片") == "半导体·存储"
    assert cb.classify("CPO") == "AI算力·液冷"


def test_empty_and_unknown():
    assert cb.classify("") == cb.OTHER
    assert cb.classify("某冷门小众题材") == cb.OTHER
