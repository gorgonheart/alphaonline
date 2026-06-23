# def signal(*args):

# 	df = args[0]
# 	n  = args[1]
# 	factor_name = args[2]
# 	df['该小时涨跌幅']=abs(df['close'].pct_change(1))
# 	df[factor_name] =df['该小时涨跌幅'].rolling(n).max()

# 	return df

# def get_parameter():
#     param_list = []

#     n_list = [24]

#     for n in n_list:
#         param_list.append(n)

#     return param_list

def signal_multi_params(df,param_list)-> dict: 
# 使用同因子多参数聚合计算，可以有效提升回测、实盘 cal_factor 的速度,
# 相对于signal'大概提升3倍左右
# :param df:k线数据的dataframe
# :param paramlist:参数列表U8 
	ret = dict()
	zdf = abs(df['close'].pct_change(1))
	for param in param_list:
		n = int(param)
		ret[str(param)]= zdf.rolling(n).max()
	return ret