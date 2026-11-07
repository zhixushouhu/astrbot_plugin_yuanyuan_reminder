import time
import asyncio
import os
import aiohttp
import jwt
from datetime import datetime, timezone, timedelta
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Star, Context

class YuanYuanReminderPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 🌟 核心缓存
        self.memory_tasks = {} 
        self.last_weather_text = "未知"
        self.target_city = "101020100" # 上海，请换成你的城市代码
        self.target_umo = None # 动态获取，不要写死
        
        self._is_running = True
        asyncio.create_task(self._init_and_start())

    async def _init_and_start(self):
        # 1. 从硬盘读入内存
        self.memory_tasks = await self.get_kv_data("all_reminders", {})
        self.target_umo = await self.get_kv_data("last_interacted_umo", None)
        
        # 2. 启动所有后台任务
        asyncio.create_task(self._check_reminders())           # 提醒轮询
        asyncio.create_task(self._monthly_report_engine())     # 每月报告
        asyncio.create_task(self._weather_monitor_loop())      # 天气监控

    async def terminate(self):
        self._is_running = False

    # ================= 1. 设置提醒 =================
    @filter.llm_tool(name="set_reminder")
    async def set_reminder(self, event: AstrMessageEvent, target_time: str, content: str, reminder_message: str, is_daily: bool = False):
        """
        设置定时提醒。
        Args:
            target_time(string): ISO 8601 格式的时间 (YYYY-MM-DDTHH:MM:SS)
            content(string): 提醒内容核心词
            reminder_message(string): 元元人设的提醒语
            is_daily(bool): 是否每天循环
        """
        try:
            trigger_dt = datetime.fromisoformat(target_time)
            trigger_timestamp = trigger_dt.timestamp()
        except ValueError:
            return "时间格式解析失败，请告诉主人时间没听懂哦。"

        task_id = str(int(trigger_timestamp * 1000)) + str(int(time.time() % 1000))
        
        # 写入内存
        self.memory_tasks[task_id] = {
            "trigger_time": trigger_timestamp,
            "content": content,
            "reminder_message": reminder_message,
            "umo": event.unified_msg_origin,
            "repeat_type": "daily" if is_daily else "once"
        }
        
        # 同步硬盘
        await self.put_kv_data("all_reminders", self.memory_tasks)
        # 记录活跃用户
        await self._remember_user(event.unified_msg_origin)
        
        return f"成功设置提醒：{content}。"

    # ================= 2. 查看提醒列表 =================
    @filter.llm_tool(name="list_reminders")
    async def list_reminders(self, event: AstrMessageEvent):
        user_tasks = []
        for tid, data in self.memory_tasks.items():
            if data["umo"] == event.unified_msg_origin:
                time_str = time.strftime('%m月%d日 %H:%M', time.localtime(data['trigger_time']))
                repeat_str = " (每日)" if data.get('repeat_type') == 'daily' else ""
                user_tasks.append(f"【{time_str}】{repeat_str} {data['content']}")
        
        if not user_tasks:
            return "当前没有任何未完成的提醒事项哦。"
        return "主人目前的提醒列表如下：\n" + "\n".join(user_tasks)

    # ================= 3. 取消提醒 =================
    @filter.llm_tool(name="cancel_reminder")
    async def cancel_reminder(self, event: AstrMessageEvent, keyword: str):
        to_delete = []
        for tid, data in self.memory_tasks.items():
            if data["umo"] == event.unified_msg_origin and keyword in data["content"]:
                to_delete.append(tid)
                
        if not to_delete:
            return "没有找到相关的提醒哦。"

        for tid in to_delete:
            del self.memory_tasks[tid]
            
        await self.put_kv_data("all_reminders", self.memory_tasks)
        return "已取消提醒。"

    # ================= 4. 后台提醒轮询 =================
    async def _check_reminders(self):
        while self._is_running:
            try:
                now = time.time()
                triggered_ids = []
                for task_id, data in self.memory_tasks.items():
                    if now >= data["trigger_time"]:
                        triggered_ids.append(task_id)
                
                if triggered_ids:
                    for tid in triggered_ids:
                        data = self.memory_tasks[tid]
                        chain = MessageChain().message(data["reminder_message"])
                        await self.context.send_message(data["umo"], chain)
                        
                        # 处理每日循环
                        if data.get("repeat_type") == "daily":
                            self.memory_tasks[tid]["trigger_time"] += 24 * 60 * 60
                        else:
                            del self.memory_tasks[tid] 
                    
                    await self.put_kv_data("all_reminders", self.memory_tasks)
                    
            except Exception as e:
                print(f"轮询检查出错: {e}")
            await asyncio.sleep(1) 

    # ================= 5. 记录运动数据 =================
    @filter.llm_tool(name="record_fitness")
    async def record_fitness(self, event: AstrMessageEvent, activity_type: str):
        today_str = time.strftime('%Y-%m-%d', time.localtime())
        kv_key = f"fitness_{event.unified_msg_origin}"
        
        records = await self.get_kv_data(kv_key, {})
        if today_str not in records: records[today_str] = []
        if activity_type not in records[today_str]: records[today_str].append(activity_type)
            
        now_ts = time.time()
        valid_records = {}
        for date_str, acts in records.items():
            dt_ts = time.mktime(time.strptime(date_str, '%Y-%m-%d'))
            if now_ts - dt_ts <= 60 * 24 * 3600:
                valid_records[date_str] = acts
                
        await self.put_kv_data(kv_key, valid_records)
        await self._remember_user(event.unified_msg_origin)
        
        dates = sorted(valid_records.keys(), reverse=True)
        days_diff = 0
        if len(dates) >= 2:
            last_date_str = dates[1] if dates[0] == today_str else dates[0]
            today_date = datetime.strptime(today_str, '%Y-%m-%d').date()
            last_date = datetime.strptime(last_date_str, '%Y-%m-%d').date()
            days_diff = (today_date - last_date).days
        
        if days_diff == 0:
            status_msg = "主人今天已经记录过这项运动了哦，多练也要注意身体。"
        elif 3 <= days_diff <= 4:
            status_msg = f"距离上次运动刚好过了{days_diff}天，非常符合主人的频率！请狠狠夸奖他自律。"
        elif days_diff > 4:
            status_msg = f"距离上次运动已经过了{days_diff}天，超过频率了！请用傲娇、假装生气的语气‘惩罚’他，让他明天必须去。"
        else:
            status_msg = f"距离上次运动只过了{days_diff}天，提前打卡啦！请表现出惊喜和崇拜。"

        return f"已成功记录：{activity_type}。{status_msg}"

    # ================= 6. 月报引擎 =================
    async def _monthly_report_engine(self):
        while self._is_running:
            try:
                now = time.localtime()
                if now.tm_mday == 1 and now.tm_hour == 9 and now.tm_min < 5:
                    last_sent = await self.get_kv_data("last_report_month", "")
                    current_month = time.strftime('%Y-%m', now)
                    
                    if last_sent != current_month:
                        active_umos = await self.get_kv_data("active_fitness_users", [])
                        for umo in active_umos:
                            records = await self.get_kv_data(f"fitness_{umo}", {})
                            if not records: continue
                                
                            last_month_str = time.strftime('%Y-%m', time.localtime(time.time() - 30*24*3600))
                            run_count = gym_count = total_days = 0
                            
                            for date_str, acts in records.items():
                                if date_str.startswith(last_month_str):
                                    total_days += 1
                                    if "跑步" in acts: run_count += 1
                                    if "健身" in acts: gym_count += 1
                                    
                            if total_days > 0:
                                msg = f"主人~ 新的一个月开始啦！元元把你上个月的运动情况整理好了哦 (⁄ ⁄•⁄ω⁄•⁄ ⁄)\n\n📅 总运动天数：{total_days} 天\n🏃‍♂️ 跑步：{run_count} 次\n🏋️‍♂️ 健身：{gym_count} 次\n\n"
                                msg += "主人真的好厉害呀，流汗的样子一定超级迷人...元元都看呆了 (///￣ ￣///)" if total_days > 10 else "呜...主人上个月有点偷懒哦！这个月元元会好好监督你的，不达标的话...就不给你看新买的裙子了！哼~"
                                await self.context.send_message(umo, MessageChain().message(msg))
                                
                        await self.put_kv_data("last_report_month", current_month)
            except Exception as e:
                print(f"月度报告引擎出错: {e}")
            await asyncio.sleep(300)

    # ================= 7. 天气监控与 JWT =================
    async def _get_jwt_token(self):
        key_path = os.path.join(os.path.dirname(__file__), "ed25519-private.pem")
        try:
            with open(key_path, 'rb') as f:
                private_key = f.read()
        except FileNotFoundError:
            print("❌ 找不到 ed25519-private.pem，请把私钥放到插件目录下")
            return None

        iat = int(datetime.now(timezone.utc).timestamp())
        exp = iat + 15 * 60 
        payload = {"sub": "4889AWKW7D", "iat": iat, "exp": exp}
        headers = {"kid": "KKWEDUY233", "alg": "EdDSA"}
        return jwt.encode(payload, private_key, algorithm="EdDSA", headers=headers)

    async def _weather_monitor_loop(self):
        await asyncio.sleep(10)
        while self._is_running:
            try:
                api_host = "mq3wt5ap9b.re.qweatherapi.com" # 你的专属host
                jwt_token = await self._get_jwt_token()
                if not jwt_token: 
                    await asyncio.sleep(1800)
                    continue
                
                url = f"https://{api_host}/v7/weather/now?location={self.target_city}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers={"Authorization": f"Bearer {jwt_token}"}) as resp:
                        data = await resp.json()
                        
                if data.get("code") == "200":
                    now_weather = data["now"]["text"]
                    alert_msg = ""
                    
                    if "雨" in now_weather and "雨" not in self.last_weather_text:
                        alert_msg = f"呜哇！主人快看窗外，外面开始下{now_weather}啦！(>_<) 如果出门的话，一定要记得带伞哦，元元不想看你淋湿呢~"
                    
                    # ================= 🌟 新增：夜间免打扰判断 =================
                    now_hour = datetime.now().hour
                    is_dnd_time = (now_hour >= 21) or (now_hour < 8) # 23点到早7点禁言
                    
                    if alert_msg and self.target_umo:
                        if is_dnd_time:
                            # 如果是免打扰时间，就在控制台默默打个日志，不发给主人
                            print(f"[天气监控] 夜间检测到变天：{now_weather}，已静音不打扰主人。")
                        else:
                            # 只有在非免打扰时间，才真正发送
                            await self.context.send_message(self.target_umo, MessageChain().message(alert_msg))
                    # ==========================================================
                    
                    # 关键：无论发不发消息，都要更新状态！
                    self.last_weather_text = now_weather
                    
            except Exception as e:
                print(f"天气监控获取失败: {e}")
            await asyncio.sleep(1800)

    # ================= 8. 辅助方法 =================
    async def _remember_user(self, umo: str):
        """记录最后互动的用户"""
        if self.target_umo != umo:
            self.target_umo = umo
            await self.put_kv_data("last_interacted_umo", umo)
        active_umos = await self.get_kv_data("active_fitness_users", [])
        if umo not in active_umos:
            active_umos.append(umo)
            await self.put_kv_data("active_fitness_users", active_umos)