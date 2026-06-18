import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import time
import datetime
import random
import json
import os
import platform
from typing import Dict, List, Optional
from gtts import gTTS  # 追加

# ==========================================
# 設定エリア (実際のIDやトークンに書き換えてください)
# ==========================================
TOKEN = 'MTUxNzA3MzE2ODQ4Nzk0NDIzMg.GGrqv0.Wl1_PPkmsXhHuY7yQrJ4XVXVhvn2LRW6mnElKU'
ROLE_ID = 1517073657921277982         # 認証時に付与するロールID
LOG_CHANNEL_ID = 1517073823877562468  # 荒らし処罰ログを流すチャンネルID

BANNED_WORDS = ["荒らし", "あらし", "spam_word_here"]
SPAM_INTERVAL = 5
SPAM_LIMIT = 5
SPAM_VIOLATION_LIMIT = 3            # スパム警告が何回累積したらタイムアウトさせるか

DATA_FILE = "bot_local_db.json"

# グローバルデータ
db = {
    "violations": {},
    "economy": {},
    "reminders": {},
    "analytics": {"total_messages": 0, "commands_executed": 0} 
}

user_message_timers: Dict[int, List[float]] = {}
BOT_START_TIME = datetime.datetime.now()

SHOP_ITEMS = {
    "👑 大富豪の称号": {"role_id": 1517073971781046422, "price": 5000},
    "✨ サーバーの常連": {"role_id": 1517074201104875570, "price": 1500},
}

# TTSの読み上げ状態を管理する辞書 {1495300241434349678: 1517074459146846288}
tts_channels: Dict[int, int] = {}


# --- 0. 高信頼性ローカルデータベースシステム ---
def load_db():
    global db
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
                if "analytics" not in db:
                    db["analytics"] = {"total_messages": 0, "commands_executed": 0}
                if "violations" not in db: db["violations"] = {}
                if "economy" not in db: db["economy"] = {}
                if "reminders" not in db: db["reminders"] = {}
        except Exception as e:
            print(f"⚠️ DB読み込み失敗、初期化します: {e}")

def save_db():
    temp_file = f"{DATA_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=4)
        os.replace(temp_file, DATA_FILE)
    except Exception as e:
        print(f"⚠️ DBの保存中にエラーが発生しました: {e}")

load_db()


# --- 1. 認証用永続ビュー ---
class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='サーバー認証を行う', style=discord.ButtonStyle.green, custom_id='verify_button_v5')
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = interaction.guild.get_role(ROLE_ID)
        if not role:
            await interaction.response.send_message('❌ エラー: 管理者によって設定されたロールが見つかりません。', ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message('ℹ️ すでに認証が完了しています。', ephemeral=True)
            return
        await interaction.user.add_roles(role)
        await interaction.response.send_message('🔒 認証が成功しました！サーバーをお楽しみください。', ephemeral=True)


# --- 2. 各種機能モジュール ---

class Moderation(commands.Cog):
    """モデレーションシステム（自動＆手動処罰）"""
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    async def punish_user(message: discord.Message, member: discord.Member, reason: str):
        uid_str = str(member.id)
        db["violations"][uid_str] = db["violations"].get(uid_str, 0) + 1
        await message.delete()
        
        log_channel = message.guild.get_channel(LOG_CHANNEL_ID)
        
        if db["violations"][uid_str] >= SPAM_VIOLATION_LIMIT:
            duration = datetime.timedelta(days=1)
            await member.timeout(duration, reason=reason)
            db["violations"][uid_str] = 0
            
            if log_channel:
                embed = discord.Embed(title="🚨 自動処罰システム：タイムアウト執行", color=discord.Color.red())
                embed.add_field(name="対象ユーザー", value=member.mention, inline=True)
                embed.add_field(name="執行理由", value=reason, inline=True)
                embed.set_timestamp()
                await log_channel.send(embed=embed)
            await message.channel.send(f"🚨 {member.mention} はスパム・荒らし行為の累積により**24時間タイムアウト**となりました。")
        else:
            left_count = SPAM_VIOLATION_LIMIT - db["violations"][uid_str]
            await message.channel.send(f"⚠️ {member.mention} {reason}を確認。（あと {left_count} 回の警告でタイムアウト執行）", delete_after=5)
        save_db()

    @app_commands.command(name="verify-panel", description="ボタン式の認証パネルを設置します(管理者用)")
    @app_commands.default_permissions(administrator=True)
    async def verify_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🔒 ゲートウェイセキュリティ認証", 
            description="下の「サーバー認証を行う」ボタンを押すことで、指定のロールが割り当てられ全コンテンツへのアクセスが認可されます。",
            color=discord.Color.green()
        )
        await interaction.response.send_message("認証パネルを展開しました。", ephemeral=True)
        await interaction.channel.send(embed=embed, view=VerifyView())

    @app_commands.command(name="clear", description="指定された件数のメッセージを一括削除します")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(amount="削除するメッセージ数(1-100)")
    async def clear_command(self, interaction: discord.Interaction, amount: int):
        if amount < 1 or amount > 100:
            await interaction.response.send_message("❌ 1から100の間の数値を指定してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"🧹 チャンネル内のメッセージを `{len(deleted)}` 件クリーンアップしました！")

    @app_commands.command(name="kick", description="指定したメンバーをサーバーからキックします")
    @app_commands.default_permissions(kick_members=True)
    async def kick_command(self, interaction: discord.Interaction, member: discord.Member, reason: str = "なし"):
        await member.kick(reason=reason)
        await interaction.response.send_message(f"👢 {member.mention} を追放(キック)しました。 理由: {reason}")

    @app_commands.command(name="ban", description="指定したメンバーをサーバーから永久追放(BAN)します")
    @app_commands.default_permissions(ban_members=True)
    async def ban_command(self, interaction: discord.Interaction, member: discord.Member, reason: str = "なし"):
        await member.ban(reason=reason)
        await interaction.response.send_message(f"🔨 {member.mention} を永久追放(BAN)しました。 理由: {reason}")

    @app_commands.command(name="mute", description="対象ユーザーを手動で指定時間タイムアウト(ミュート)します")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(minutes="ミュートする時間(分単位)")
    async def mute_command(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "管理者による手動処罰"):
        duration = datetime.timedelta(minutes=minutes)
        await member.timeout(duration, reason=reason)
        await interaction.response.send_message(f"🤫 {member.mention} を `{minutes}分間` タイムアウトしました。 理由: {reason}")

    @app_commands.command(name="unmute", description="対象ユーザーのタイムアウト(ミュート)を早期解除します")
    @app_commands.default_permissions(moderate_members=True)
    async def unmute_command(self, interaction: discord.Interaction, member: discord.Member):
        await member.timeout(None, reason="管理者による処罰解除")
        await interaction.response.send_message(f"🔊 {member.mention} のタイムアウトを解除しました。発言が可能です。")


class Information(commands.Cog):
    """インフォメーション＆アナリティクス"""
    def __init__(self, bot):
        self.bot = bot

    info_group = app_commands.Group(name="info", description="各種アナリティクス・ステータス情報を抽出します")

    @info_group.command(name="bot", description="Botの内部ステータスおよびシステムリソースを分析")
    async def info_bot(self, interaction: discord.Interaction):
        uptime = datetime.datetime.now() - BOT_START_TIME
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        embed = discord.Embed(title=f"🧠 {self.bot.user.name} インテリジェンスレポート", color=discord.Color.blue())
        embed.add_field(name="⏱️ システム稼楽時間", value=f"`{uptime.days}日 {hours}時間 {minutes}分 {seconds}秒`", inline=False)
        embed.add_field(name="📡 レイテンシ(WS)", value=f"`{round(self.bot.latency * 1000)}ms`", inline=True)
        embed.add_field(name="⚙️ プラットフォーム", value=f"Python `{platform.python_version()}`\nOS `{platform.system()}`", inline=False)
        await interaction.response.send_message(embed=embed)

    @info_group.command(name="server", description="現在のサーバーインフラの統計情報を開示")
    async def info_server(self, interaction: discord.Interaction):
        guild = interaction.guild
        tc_count = len(guild.text_channels)
        vc_count = len(guild.voice_channels)
        
        embed = discord.Embed(title=f"📊 {guild.name} インフラマトリクス", color=discord.Color.purple())
        embed.add_field(name="👑 オーナー", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="👥 人口統計", value=f"総メンバー: `{guild.member_count}名`", inline=True)
        embed.add_field(name="💬 チャンネル構成", value=f"テキスト: `{tc_count}` / ボイス: `{vc_count}`", inline=False)
        embed.set_footer(text=f"Server ID: {guild.id}")
        await interaction.response.send_message(embed=embed)

    @info_group.command(name="user", description="対象メンバのアカウントデータおよびBot内戦績データをロード")
    async def info_user(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        uid_str = str(target.id)
        
        points = db["economy"].get(uid_str, {}).get("points", 0)
        violations = db["violations"].get(uid_str, 0)
        
        embed = discord.Embed(title=f"👤 ユーザーカルテ: {target.display_name}", color=target.color)
        embed.add_field(name="🆔 識別子", value=f"{target} (`{target.id}`)", inline=False)
        embed.add_field(name="📆 アカウント創設", value=discord.utils.format_dt(target.created_at, 'R'), inline=True)
        embed.add_field(name="🪙 資産残高", value=f"`{points} Pt`", inline=True)
        embed.add_field(name="⚠️ 警告インデックス", value=f"`{violations} 回`", inline=True)
        await interaction.response.send_message(embed=embed)

    @info_group.command(name="analytics", description="サーバー内のアクティビティ推移を表示します")
    async def info_analytics(self, interaction: discord.Interaction):
        total_msg = db["analytics"].get("total_messages", 0)
        total_cmd = db["analytics"].get("commands_executed", 0)
        
        embed = discord.Embed(title="📈 サーバーアクティビティ・アナリティクス", color=discord.Color.teal())
        embed.add_field(name="💬 累積メッセージ処理数", value=f"`{total_msg} msg`", inline=True)
        embed.add_field(name="🤖 累積スラッシュコマンド実行数", value=f"`{total_cmd}回`", inline=True)
        embed.set_timestamp()
        await interaction.response.send_message(embed=embed)


class Casino(commands.Cog):
    """本格カジノシステム"""
    def __init__(self, bot):
        self.bot = bot

    casino_group = app_commands.Group(name="casino", description="サーバー内ポイントを賭けたゲームをプレイします")

    @staticmethod
    def draw_card():
        cards = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10]
        return random.choice(cards)

    @staticmethod
    def calculate_score(hand: List[int]) -> int:
        score = sum(hand)
        aces = hand.count(1)
        while score <= 11 and aces > 0:
            score += 10
            aces -= 1
        return score

    @casino_group.command(name="slots", description="スロットマシンを回転させます")
    async def slots_command(self, interaction: discord.Interaction, bet: int):
        uid = str(interaction.user.id)
        user_data = db["economy"].get(uid, {"points": 100, "last_daily": ""})
        
        if bet <= 0 or user_data["points"] < bet:
            await interaction.response.send_message("❌ エラー: 指定されたポイントが不正、または残高が不足しています。", ephemeral=True)
            return
            
        user_data["points"] -= bet
        emojis = ["🍒", "🍇", "🍋", "💎", "7️⃣"]
        result = [random.choice(emojis) for _ in range(3)]
        
        if result[0] == result[1] == result[2]:
            payout = bet * 10 if result[0] == "7️⃣" else bet * 4
            msg = f"🎰 **JACKPOT!!** 残高が `{payout} Pt` 増加。"
        elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
            payout = int(bet * 1.5)
            msg = f"✨ **ワンペア配当獲得。** 残高が `{payout} Pt` 増加。"
        else:
            payout = 0
            msg = "💸 **ハウス（胴元）の勝ちです。**"
            
        user_data["points"] += payout
        db["economy"][uid] = user_data
        save_db()
        await interaction.response.send_message(f"【 ｜ {' ｜ '.join(result)} ｜ 】\n\n{interaction.user.mention} {msg}\n🪙 現在の資産: **{user_data['points']} Pt**")

    @casino_group.command(name="blackjack", description="ディーラーと21を競うブラックジャックをプレイします")
    async def blackjack_command(self, interaction: discord.Interaction, bet: int):
        uid = str(interaction.user.id)
        if db["economy"].get(uid, {}).get("points", 0) < bet or bet <= 0:
            await interaction.response.send_message("❌ 残高が不足しているか、賭け金が不正です。", ephemeral=True)
            return

        db["economy"][uid]["points"] -= bet
        save_db()

        class BlackjackView(discord.ui.View):
            def __init__(self, casino_cog, user_id, bet_amount):
                super().__init__(timeout=60)
                self.cog = casino_cog
                self.user_id = user_id
                self.bet = bet_amount
                self.player_hand = [self.cog.draw_card(), self.cog.draw_card()]
                self.dealer_hand = [self.cog.draw_card(), self.cog.draw_card()]

            async def get_embed(self, status: str, finished: bool = False):
                p_score = self.cog.calculate_score(self.player_hand)
                d_score = self.cog.calculate_score(self.dealer_hand)
                embed = discord.Embed(title="🃏 ブラックジャックテーブル", color=discord.Color.dark_green())
                embed.add_field(name="🫵 あなたのハンド", value=f"カード: {self.player_hand}\nスコア: `{p_score}`", inline=True)
                if finished:
                    embed.add_field(name="🤖 ディーラーのハンド", value=f"カード: {self.dealer_hand}\nスコア: `{d_score}`", inline=True)
                else:
                    embed.add_field(name="🤖 ディーラーのハンド", value=f"カード: [{self.dealer_hand[0]}, ?]\nスコア: `?`", inline=True)
                embed.set_footer(text=status)
                return embed

            @discord.ui.button(label="ヒット", style=discord.ButtonStyle.primary)
            async def hit(self, inter: discord.Interaction, button: discord.ui.Button):
                if inter.user.id != self.user_id: 
                    await inter.response.send_message("❌ これはあなたのゲームではありません。", ephemeral=True)
                    return
                self.player_hand.append(self.cog.draw_card())
                if self.cog.calculate_score(self.player_hand) > 21:
                    self.stop()
                    await inter.response.edit_message(embed=await self.get_embed("💥 バースト！あなたの負けです。", finished=True), view=None)
                else:
                    await inter.response.edit_message(embed=await self.get_embed("もう一枚引きますか？"), view=self)

            @discord.ui.button(label="スタンド", style=discord.ButtonStyle.secondary)
            async def stand(self, inter: discord.Interaction, button: discord.ui.Button):
                if inter.user.id != self.user_id: 
                    await inter.response.send_message("❌ これはあなたのゲームではありません。", ephemeral=True)
                    return
                self.stop()
                while self.cog.calculate_score(self.dealer_hand) < 17:
                    self.dealer_hand.append(self.cog.draw_card())
                
                p_score = self.cog.calculate_score(self.player_hand)
                d_score = self.cog.calculate_score(self.dealer_hand)
                p_uid = str(self.user_id)

                if d_score > 21 or p_score > d_score:
                    status = f"🎉 勝ちました！ `+{self.bet * 2} Pt` 還元！"
                    db["economy"][p_uid]["points"] += self.bet * 2
                elif p_score < d_score:
                    status = "💸 ディーラーの勝利です。"
                else:
                    status = "🤝 引き分け（プッシュ）。払い戻されます。"
                    db["economy"][p_uid]["points"] += self.bet
                save_db()
                await inter.response.edit_message(embed=await self.get_embed(status, finished=True), view=None)

        view = BlackjackView(self, interaction.user.id, bet)
        await interaction.response.send_message(embed=await view.get_embed("ヒットかスタンドか選択してください。"), view=view)

    @casino_group.command(name="coinflip", description="コインの表裏を予想して賭け金を2倍にするか失うかの勝負をします")
    @app_commands.choices(choice=[
        app_commands.Choice(name="表 (Heads)", value="heads"),
        app_commands.Choice(name="裏 (Tails)", value="tails")
    ])
    async def coinflip_command(self, interaction: discord.Interaction, choice: str, bet: int):
        uid = str(interaction.user.id)
        points = db["economy"].get(uid, {}).get("points", 0)
        if bet <= 0 or points < bet:
            await interaction.response.send_message("❌ 残高が不足しているか、無効なベット額です。", ephemeral=True)
            return
            
        db["economy"][uid]["points"] -= bet
        result = random.choice(["heads", "tails"])
        win = (choice == result)
        
        result_emoji = "🪙 (表)" if result == "heads" else "🪙 (裏)"
        if win:
            db["economy"][uid]["points"] += bet * 2
            msg = f"🎉 見事に的中！ **+{bet * 2} Pt** を獲得しました！"
        else:
            msg = f"💸 残念、外れました。賭け金 `{bet} Pt` は没収されました。"
            
        save_db()
        await interaction.response.send_message(f"【 コイン結果: {result_emoji} 】\n{interaction.user.mention} {msg}")


class Economy(commands.Cog):
    """経済＆ポイントエコシステム"""
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="daily", description="1日1回ログインボーナスポイントを獲得します")
    async def daily_command(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        if uid not in db["economy"]: db["economy"][uid] = {"points": 100, "last_daily": ""}
        today = datetime.date.today().isoformat()
        if db["economy"][uid]["last_daily"] == today:
            await interaction.response.send_message("❌ 本日のデイリーボーナスは受領済みです。", ephemeral=True)
            return
        bonus = random.randint(200, 600)
        db["economy"][uid]["points"] += bonus
        db["economy"][uid]["last_daily"] = today
        save_db()
        await interaction.response.send_message(f"🎉 ログイン成功。 **+{bonus} Pt** をウォレットに追加しました。")

    @app_commands.command(name="wallet", description="現在の所持ポイントを確認します")
    async def wallet_command(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        points = db["economy"].get(uid, {}).get("points", 100)
        await interaction.response.send_message(f"🪙 {interaction.user.mention} さんの現在の資産高: **{points} Pt**")

    @app_commands.command(name="pay", description="指定したメンバーに自分のポイントを送金(譲渡)します")
    async def pay_command(self, interaction: discord.Interaction, receiver: discord.Member, amount: int):
        if receiver.bot or receiver.id == interaction.user.id:
            await interaction.response.send_message("❌ 自分自身やBotに送金することはできません。", ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message("❌ 送金額は1以上の整数にしてください。", ephemeral=True)
            return
            
        sender_id = str(interaction.user.id)
        receiver_id = str(receiver.id)
        sender_points = db["economy"].get(sender_id, {}).get("points", 100)
        
        if sender_points < amount:
            await interaction.response.send_message("❌ 残高が不足しているため送金できません。", ephemeral=True)
            return
            
        if receiver_id not in db["economy"]:
            db["economy"][receiver_id] = {"points": 100, "last_daily": ""}
            
        db["economy"][sender_id]["points"] -= amount
        db["economy"][receiver_id]["points"] += amount
        save_db()
        await interaction.response.send_message(f"💸 {interaction.user.mention} から {receiver.mention} へ **{amount} Pt** の個人間送金が完了しました！")

    @app_commands.command(name="shop", description="貯めたポイントを消費して限定ロールを購入します")
    @app_commands.describe(item_name="ショップ内のアイテム名")
    async def shop_command(self, interaction: discord.Interaction, item_name: str):
        if item_name not in SHOP_ITEMS:
            item_list_str = "\n".join([f"・**{k}** : {v['price']} Pt" for k, v in SHOP_ITEMS.items()])
            await interaction.response.send_message(f"🏪 **サーバーポイント・特設ショップ**\nアイテム名を正確に入力してください:\n{item_list_str}", ephemeral=True)
            return

        uid = str(interaction.user.id)
        user_points = db["economy"].get(uid, {}).get("points", 100)
        target_item = SHOP_ITEMS[item_name]

        if user_points < target_item["price"]:
            await interaction.response.send_message(f"❌ 残高が足りません。あと `{target_item['price'] - user_points} Pt` 必要です。", ephemeral=True)
            return

        role = interaction.guild.get_role(target_item["role_id"])
        if not role:
            await interaction.response.send_message("❌ 設定エラー: ショップロールがサーバー内に見つかりません。", ephemeral=True)
            return

        if role in interaction.user.roles:
            await interaction.response.send_message("ℹ️ あなたは既にこのロール（商品）を所有しています。", ephemeral=True)
            return

        db["economy"][uid]["points"] -= target_item["price"]
        save_db()
        await interaction.user.add_roles(role)
        await interaction.response.send_message(f"🛍️ 購入成功！ **{item_name}** を購入したため、ロール【{role.name}】がインベントリに付与されました！")

    @app_commands.command(name="leaderboard", description="サーバー内の所持ポイント長者番付ランキングを表示します")
    async def leaderboard_command(self, interaction: discord.Interaction):
        raw_data = db["economy"]
        sorted_users = sorted(raw_data.items(), key=lambda item: item[1].get("points", 0), reverse=True)[:5]
        
        embed = discord.Embed(title="👑 サーバー資産長者番付トップ5", color=discord.Color.gold())
        for idx, (uid_str, data) in enumerate(sorted_users, start=1):
            member = interaction.guild.get_member(int(uid_str))
            name = member.display_name if member else f"不帰のユーザー({uid_str})"
            embed.add_field(name=f"第{idx}位: {name}", value=f"資産: `{data.get('points', 0)} Pt`", inline=False)
        await interaction.response.send_message(embed=embed)


class Utilities(commands.Cog):
    """ユーティリティ＆エンタメ"""
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    async def reminder_task(user_id: str, delay: float, content: str, reminder_id: float, channel_id: int, bot):
        if delay > 0:
            await asyncio.sleep(delay)
        
        uid = str(user_id)
        if uid in db["reminders"]:
            if any(r['id'] == reminder_id for r in db["reminders"][uid]):
                db["reminders"][uid] = [r for r in db["reminders"][uid] if r['id'] != reminder_id]
                save_db()
                channel = bot.get_channel(channel_id)
                if channel:
                    await channel.send(f"🔔 <@{user_id}> **リマインダーシステム通知**\n📌 **内容:** {content}")

    @app_commands.command(name="omikuji", description="今日のおみくじを引いて運勢を占います")
    async def omikuji_command(self, interaction: discord.Interaction):
        fortunes = [
            ("大吉", "🎉 最高の一日！何をやってもうまくいきます。"),
            ("吉", "✨ 安定して良い日。お気に入りの音楽を聴くとさらに運気アップ。"),
            ("中吉", "👍 良いことに出会える予感。周囲の人に優しくすると吉。"),
            ("凶", "☔️ 今日はのんびり過ごすのが吉。温かい飲み物を飲んでリラックス！")
        ]
        result, desc = random.choice(fortunes)
        embed = discord.Embed(
            title=f"🔮 {interaction.user.display_name} さんの今日の運勢",
            description=f"結果は... **【{result}】** です！\n\n{desc}",
            color=discord.Color.random()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="remind", description="自動復元対応の高精度リマインダーを登録します")
    async def remind_set(self, interaction: discord.Interaction, minutes: int, content: str):
        if minutes <= 0:
            await interaction.response.send_message("❌ 正の整数を指定してください。", ephemeral=True)
            return
        delay_seconds = minutes * 60
        reminder_id = time.time()
        target_timestamp = reminder_id + delay_seconds
        uid = str(interaction.user.id)
        if uid not in db["reminders"]: db["reminders"][uid] = []
        db["reminders"][uid].append({
            "id": reminder_id,
            "time": f"{minutes}分後",
            "content": content,
            "target_timestamp": target_timestamp,
            "channel_id": interaction.channel_id
        })
        save_db()
        await interaction.response.send_message(f"⏰ リマインダーキューに登録しました。 ({minutes}分後)", ephemeral=True)
        asyncio.create_task(self.reminder_task(uid, delay_seconds, content, reminder_id, interaction.channel_id, self.bot))

    @app_commands.command(name="dice", description="指定した面数のサイコロを複数個振ります")
    @app_commands.describe(sides="サイコロの面数(例: 6, 10, 100)", rolls="振る回数")
    async def dice_command(self, interaction: discord.Interaction, sides: int = 6, rolls: int = 1):
        if sides < 2 or rolls < 1 or rolls > 20:
            await interaction.response.send_message("❌ 不正な入力です。ダイス数は1-20、面数は2以上を指定してください。", ephemeral=True)
            return
        results = [random.randint(1, sides) for _ in range(rolls)]
        await interaction.response.send_message(f"🎲 ダイスロール結果 ({rolls}d{sides}):\n`{results}` (合計値: **{sum(results)}**)")

    @app_commands.command(name="choice", description="スペース区切りで入力された選択肢からBotがランダムに1つ選びます")
    @app_commands.describe(options="例: ラーメン カレー パスタ")
    async def choice_command(self, interaction: discord.Interaction, options: str):
        choices_list = options.split()
        if len(choices_list) < 2:
            await interaction.response.send_message("❌ 半角または全角スペースで区切って、2つ以上の選択肢を入力してください。", ephemeral=True)
            return
        selected = random.choice(choices_list)
        await interaction.response.send_message(f"🤔 厳正なる抽選の結果... **【{selected}】** を選択しました！")

    @app_commands.command(name="ping", description="Botのネットワーク応答ラグ（応答速度）を確認します")
    async def ping_command(self, interaction: discord.Interaction):
        ws_latency = round(self.bot.latency * 1000)
        start_time = time.time()
        await interaction.response.send_message("📡 パケット往復測定中...")
        api_latency = round((time.time() - start_time) * 1000)
        await interaction.edit_original_response(
            content=f"🏓 **Pong!**\n・`WebSocket`ラグ (システムコア): `{ws_latency}ms`\n・`HTTP API`ラグ (通信ゲートウェイ): `{api_latency}ms`"
        )


class TTS(commands.Cog):
    """【新規追加】TTS(Text-to-Speech) 読み上げシステム"""
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="join", description="あなたが参加しているボイスチャンネルに接続し、読み上げを開始します")
    async def join_command(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ ボイスチャンネルに入室してから実行してください。", ephemeral=True)
            return

        voice_channel = interaction.user.voice.channel
        
        # すでにどこかのVCに接続している場合
        if interaction.guild.voice_client:
            if interaction.guild.voice_client.channel == voice_channel:
                await interaction.response.send_message("ℹ️ すでに現在のボイスチャンネルに接続しています。", ephemeral=True)
                return
            else:
                await interaction.guild.voice_client.move_to(voice_channel)
        else:
            await voice_channel.connect()

        # 読み上げ対象のテキストチャンネルとしてロック
        tts_channels[interaction.guild.id] = interaction.channel.id
        await interaction.response.send_message(f"🔊 {voice_channel.name} に接続しました。このチャンネルのテキストを読み上げます！")

    @app_commands.command(name="leave", description="ボイスチャンネルから切断し、読み上げを終了します")
    async def leave_command(self, interaction: discord.Interaction):
        if not interaction.guild.voice_client:
            await interaction.response.send_message("❌ Botはボイスチャンネルに接続していません。", ephemeral=True)
            return

        await interaction.guild.voice_client.disconnect()
        if interaction.guild.id in tts_channels:
            del tts_channels[interaction.guild.id]
            
        await interaction.response.send_message("👋 ボイスチャンネルから切断しました。")


# --- 3. Discord Bot メインコアクラス ---
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())

    async def setup_hook(self):
        self.add_view(VerifyView())
        
        await self.add_cog(Moderation(self))
        await self.add_cog(Information(self))
        await self.add_cog(Casino(self))
        await self.add_cog(Economy(self))
        await self.add_cog(Utilities(self))
        await self.add_cog(TTS(self))  # 新規Cog追加
        
        await self.tree.sync()
        self.loop.create_task(self.restore_reminders())

    async def restore_reminders(self):
        await self.wait_until_ready()
        now_ts = time.time()
        for uid, r_list in list(db.get("reminders", {}).items()):
            for r in r_list:
                delay = r["target_timestamp"] - now_ts
                asyncio.create_task(Utilities.reminder_task(uid, max(0.0, delay), r["content"], r["id"], r["channel_id"], self))

bot = MyBot()


# --- 4. グローバルイベント ＆ インターセプター ---
@bot.event
async def on_ready():
    print(f'👑 【超超魔改造仕様：TTS追加版】25コマンド搭載Botが覚醒しました: {bot.user}')

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # メッセージカウンター
    db["analytics"]["total_messages"] += 1

    # チャットでの経験ポイント付与
    uid = str(message.author.id)
    if uid not in db["economy"]: 
        db["economy"][uid] = {"points": 100, "last_daily": ""}
    db["economy"][uid]["points"] += 1
    save_db()

    # イースターエッグ
    if message.content.lower() == 'hi':
        await message.channel.send(f'Hello! {message.author.mention} 👋')
        return

    # 禁止ワード検知
    if any(word in message.content for word in BANNED_WORDS):
        await Moderation.punish_user(message, message.author, "禁止ワード送信の検知")
        return

    # スパムレートリミット監視
    current_time = time.time()
    user_id = message.author.id
    if user_id not in user_message_timers:
        user_message_timers[user_id] = []
    user_message_timers[user_id] = [t for t in user_message_timers[user_id] if current_time - t < SPAM_INTERVAL]
    user_message_timers[user_id].append(current_time)
    
    if len(user_message_timers[user_id]) > SPAM_LIMIT:
        await Moderation.punish_user(message, message.author, "レートリミット超過（超高速連投）")
        return

    # --- TTS 自動読み上げセクション ---
    # 設定されたテキストチャンネル、かつBotが音声接続されている場合
    if message.guild.id in tts_channels and tts_channels[message.guild.id] == message.channel.id:
        vc = message.guild.voice_client
        if vc and vc.is_connected():
            # URLや長文、メンションの簡単な整形
            text = message.clean_content
            if text.startswith("http"):
                text = "ユーアールエル"
            if len(text) > 50:
                text = text[:50] + " 以下略"
                
            if text:
                # 音声ファイル生成・再生処理 (非同期ブロックを避けるためスレッド、または簡易ランタイム生成)
                try:
                    filename = f"tts_{message.guild.id}.mp3"
                    tts = gTTS(text=f"{message.author.display_name}。 {text}", lang='ja')
                    tts.save(filename)
                    
                    # 再生中の場合は終了するまで待つか、スキップ（今回はキューなしの簡易上書き再生）
                    if vc.is_playing():
                        vc.stop()
                        
                    vc.play(discord.FFmpegPCMAudio(filename))
                except Exception as e:
                    print(f"TTS再生エラー: {e}")

    await bot.process_commands(message)

@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command: app_commands.Command or app_commands.ContextMenu):
    db["analytics"]["commands_executed"] = db["analytics"].get("commands_executed", 0) + 1
    save_db()


# --- 5. 統合エラーハンドラー ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        embed = discord.Embed(
            title="❌ 権限エラー",
            description=f"このコマンドを実行するための権限（必要な権限: `{error.missing_permissions}`）が不足しています。",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    elif isinstance(error, app_commands.BotMissingPermissions):
        embed = discord.Embed(
            title="❌ Bot権限不足",
            description=f"Bot側にコマンドを実行する権限（必要な権限: `{error.missing_permissions}`）がありません。サーバー設定を確認してください。",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        print(f"予期せぬコマンドエラー: {error}")
        embed = discord.Embed(
            title="⚠️ システムエラー",
            description="コマンドの実行中に予期せぬエラーが発生しました。",
            color=discord.Color.orange()
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, ephemeral=True)


# --- 6. 起動処理 ---
if __name__ == "__main__":
    bot.run(TOKEN)
