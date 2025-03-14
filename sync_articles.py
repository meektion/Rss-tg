import random

# 本地毒鸡汤列表
POISONOUS_SOUP_LIST = [
    "你以为努力就能成功？不，努力只是让你离失败更近一步。",
    "生活不止眼前的苟且，还有远方的苟且。",
    "失败是成功之母，但成功六亲不认。",
    "你以为有钱人很快乐吗？他们的快乐你根本想象不到。",
    "只要你肯努力，没有什么事情是你搞不砸的。",
    "有时候你不努力一下，都不知道什么叫绝望。",
    "你并不是一无所有，你还有病啊！",
    "别人都有背景，而你只有背影。",
    "你努力过后才发现，智商的鸿沟是无法逾越的。",
    "虽然你长得丑，但你想得美啊！"
]

def get_poisonous_soup():
    """获取一句毒鸡汤"""
    try:
        response = requests.get("https://api.shadiao.pro/chicken_soup")
        data = response.json()
        return data['data']['text']
    except Exception as e:
        print(f"Failed to fetch poisonous soup: {e}")
        # 如果 API 获取失败，从本地列表随机选择一句
        return random.choice(POISONOUS_SOUP_LIST)
