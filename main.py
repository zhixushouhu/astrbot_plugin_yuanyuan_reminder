from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Star, Context

class YuanyuanReminderPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        print("元元提醒插件的后台逻辑已加载~")

    async def on_start(self):
        """当 AstrBot 启动或重载该插件时执行"""
        print("元元已经准备好为服务主人啦！(^▽^)")

    @filter.command("test")
    async def test_command(self, event: AstrMessageEvent):
        """测试插件是否正常运行"""
        # 使用 yield 和 event.plain_result() 来回复消息
        yield event.plain_result("主人好呀~ 元元已经准备好啦！(^▽^) 接下来我们就可以写提醒功能了喔~")
        
    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
