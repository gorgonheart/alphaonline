"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import importlib

import pandas as pd
import polars as pl


class DummyFactor:
    """
    ！！！！抽象因子对象，仅用于代码提示！！！！
    """
    # 额外数据
    extra_data_dict: dict = {}

    # 因子模式：时序因子/截面因子（factors/sections）
    # False： 时序因子
    # True：  截面因子
    is_cross: bool = False

    def signal(self, *args) -> pd.DataFrame:
        raise NotImplementedError

    def signal_multi_params(self, df, param_list: list | set | tuple) -> dict:
        raise NotImplementedError

    def signal_polars(self, lf: pl.LazyFrame, params: tuple, factor_name: str) -> pl.LazyFrame:
        """
        Polars 版本的截面因子计算（可选实现）

        :param lf: 全量 K 线数据 (LazyFrame)
        :param params: 因子参数
        :param factor_name: 因子列名
        :return: 添加了因子列的 LazyFrame
        """
        raise NotImplementedError

    def get_factor_list(self, n):
        raise NotImplementedError


class FactorHub:
    _factor_cache = {}

    # noinspection PyTypeChecker
    @staticmethod
    def get_by_name(factor_name) -> DummyFactor:
        import locale
        # 防止部分操作系统，无法识别中文名的策略或者因子
        # 不同 OS / Python 版本 / 镜像对 locale 名字支持不同，按优先级容错尝试
        for _loc in ('en_US.UTF-8', 'English_United States.1252', 'en-US', ''):
            try:
                locale.setlocale(locale.LC_ALL, _loc)
                break
            except locale.Error:
                continue

        if factor_name in FactorHub._factor_cache:
            return FactorHub._factor_cache[factor_name]

        try:
            try:
                module = 'factors'
                factor_module = importlib.import_module(f"{module}.{factor_name}")
            except ModuleNotFoundError:
                module = 'sections'
                factor_module = importlib.import_module(f"{module}.{factor_name}")

            # 创建一个包含模块变量和函数的字典
            factor_content = {
                name: getattr(factor_module, name) for name in dir(factor_module)
                if not name.startswith("__")
            }
            factor_content['is_cross'] = module == 'sections'

            # 创建一个包含这些变量和函数的对象
            factor_instance = type(factor_name, (), factor_content)

            # 缓存策略对象
            FactorHub._factor_cache[factor_name] = factor_instance

            return factor_instance
        except ModuleNotFoundError:
            raise ValueError(f"Factor {factor_name} not found.")
        except AttributeError:
            raise ValueError(f"Error accessing factor content in module {factor_name}.")


# 使用示例
if __name__ == "__main__":
    factor = FactorHub.get_by_name("PctChange")
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
