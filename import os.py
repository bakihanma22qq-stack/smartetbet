import os  
import re  
import time  
import logging  
import requests  
from cachetools import TTLCache  
from telegram import Update  
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes  
from groq import Groq  
  
logging.basicConfig(level=logging.INFO)  
  
BOT_TOKEN = os.environ["BOT_TOKEN"]  
GROQ_API_KEY = os.environ["GROQ_API_KEY"]  
FOOTBALL_DATA_API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")  
BALLDONTLIE_API_KEY = os.environ.get("BALLDONTLIE_API_KEY", "")  
  
client = Groq(api_key=GROQ_API_KEY)  
MATCH_PATTERN = re.compile(r"(.+?)\s+(?:vs\.?|v)\s+(.+)", re.IGNORECASE)  
  
# Cache results for 15 min to save API calls / rate limits  
cache = TTLCache(maxsize=200, ttl=900)  
  
  
# ---------- SOCCER DATA (football-data.org) ----------  
  
def search_football_team(name: str):  
    try:  
        r = requests.get(  
            "https://api.football-data.org/v4/teams",  
            params={"name": name},  
            headers={"X-Auth-Token": FOOTBALL_DATA_API_KEY},  
            timeout=8,  
        )  
        if r.status_code != 200:  
            return None  
        teams = r.json().get("teams", [])  
        if not teams:  
            return None  
        # pick best match (exact or closest name)  
        name_lower = name.lower()  
        for t in teams:  
            if t["name"].lower() == name_lower or t["shortName"].lower() == name_lower:  
                return t  
        return teams[0]  
    except Exception as e:  
        logging.warning(f"Football search failed for {name}: {e}")  
        return None  
  
  
def get_football_form(team_id: int):  
    try:  
        r = requests.get(  
            f"https://api.football-data.org/v4/teams/{team_id}/matches",  
            params={"status": "FINISHED", "limit": 5},  
            headers={"X-Auth-Token": FOOTBALL_DATA_API_KEY},  
            timeout=8,  
        )  
        if r.status_code != 200:  
            return None  
        matches = r.json().get("matches", [])  
        if not matches:  
            return None  
  
        results = []  
        goals_for, goals_against, btts_count = 0, 0, 0  
  
        for m in matches:  
            home = m["homeTeam"]["id"] == team_id  
            score = m["score"]["fullTime"]  
            gf = score["home"] if home else score["away"]  
            ga = score["away"] if home else score["home"]  
            if gf is None or ga is None:  
                continue  
            goals_for += gf  
            goals_against += ga  
            if gf > 0 and ga > 0:  
                btts_count += 1  
            result = "W" if gf > ga else "D" if gf == ga else "L"  
            results.append(result)  
  
        n = len(results) or 1  
        return {  
            "form": "-".join(results) if results else "N/A",  
            "avg_goals_for": round(goals_for / n, 1),  
            "avg_goals_against": round(goals_against / n, 1),  
            "btts_pct": round((btts_count / n) * 100),  
        }  
    except Exception as e:  
        logging.warning(f"Football form fetch failed: {e}")  
        return None  
  
  
def get_soccer_stats(team_a: str, team_b: str):  
    team_a_obj = search_football_team(team_a)  
    team_b_obj = search_football_team(team_b)  
    if not team_a_obj or not team_b_obj:  
        return None  
  
    form_a = get_football_form(team_a_obj["id"])  
    form_b = get_football_form(team_b_obj["id"])  
    if not form_a or not form_b:  
        return None  
  
    return (  
        f"REAL RECENT FORM (last 5 finished matches, this season):\n"  
        f"{team_a_obj['name']}: Form {form_a['form']}, "  
        f"avg goals scored {form_a['avg_goals_for']}, "  
        f"avg goals conceded {form_a['avg_goals_against']}, "  
        f"BTTS in {form_a['btts_pct']}% of recent matches.\n"  
        f"{team_b_obj['name']}: Form {form_b['form']}, "  
        f"avg goals scored {form_b['avg_goals_for']}, "  
        f"avg goals conceded {form_b['avg_goals_against']}, "  
        f"BTTS in {form_b['btts_pct']}% of recent matches."  
    )  
  
  
# ---------- BASKETBALL DATA (balldontlie.io) ----------  
  
def get_nba_teams():  
    try:  
        r = requests.get(  
            "https://api.balldontlie.io/v1/teams",  
            headers={"Authorization": BALLDONTLIE_API_KEY},  
            timeout=8,  
        )  
        if r.status_code != 200:  
            return []  
        return r.json().get("data", [])  
    except Exception as e:  
        logging.warning(f"NBA teams fetch failed: {e}")  
        return []  
  
  
def find_nba_team(name: str, teams: list):  
    name_lower = name.lower()  
    for t in teams:  
        if name_lower in t["full_name"].lower() or name_lower in t["name"].lower():  
            return t  
    return None  
  
  
def get_nba_form(team_id: int):  
    try:  
        current_year = time.gmtime().tm_year  
        r = requests.get(  
            "https://api.balldontlie.io/v1/games",  
            params={"team_ids[]": team_id, "seasons[]": current_year, "per_page": 5},  
            headers={"Authorization": BALLDONTLIE_API_KEY},  
            timeout=8,  
        )  
        if r.status_code != 200:  
            return None  
        games = r.json().get("data", [])  
        if not games:  
            return None  
  
        games.sort(key=lambda g: g["date"], reverse=True)  
        results, pts_for, pts_against = [], 0, 0  
  
        for g in games[:5]:  
            home = g["home_team"]["id"] == team_id  
            pf = g["home_team_score"] if home else g["visitor_team_score"]  
            pa = g["visitor_team_score"] if home else g["home_team_score"]  
            pts_for += pf  
            pts_against += pa  
            results.append("W" if pf > pa else "L")  
  
        n = len(results) or 1  
        return {  
            "form": "-".join(results),  
            "avg_pts_for": round(pts_for / n, 1),  
            "avg_pts_against": round(pts_against / n, 1),  
        }  
    except Exception as e:  
        logging.warning(f"NBA form fetch failed: {e}")  
        return None  
  
  
def get_basketball_stats(team_a: str, team_b: str):  
    teams = get_nba_teams()  
    if not teams:  
        return None  
  
    team_a_obj = find_nba_team(team_a, teams)  
    team_b_obj = find_nba_team(team_b, teams)  
    if not team_a_obj or not team_b_obj:  
        return None  
  
    form_a = get_nba_form(team_a_obj["id"])  
    form_b = get_nba_form(team_b_obj["id"])  
    if not form_a or not form_b:  
        return None  
  
    return (  
        f"REAL RECENT FORM (last 5 games, this season):\n"  
        f"{team_a_obj['full_name']}: Form {form_a['form']}, "  
        f"avg points scored {form_a['avg_pts_for']}, "  
        f"avg points allowed {form_a['avg_pts_against']}.\n"  
        f"{team_b_obj['full_name']}: Form {form_b['form']}, "  
        f"avg points scored {form_b['avg_pts_for']}, "  
        f"avg points allowed {form_b['avg_pts_against']}."  
    )  
  
  
# ---------- PROMPT + LLM ----------  
  
def build_prompt(team_a: str, team_b: str, real_stats: str | None) -> str:  
    stats_block = (  
        real_stats  
        if real_stats  
        else "No live stats were found for this matchup — base your suggestions on general football/basketball knowledge."  
    )  
  
    return f"""You are a sharp sports betting analyst. A user wants betting suggestions  
for the match: {team_a} vs {team_b}.  
  
{stats_block}  
  
First, identify if this is SOCCER or BASKETBALL based on the team names.  
  
Then give exactly 5 realistic, smart betting market suggestions, using the real stats  
above if provided to justify your picks. Mix market types.  
  
Soccer options could include: Both Teams to Score, Over/Under Goals, Draw No Bet,  
Handicap, Correct Score, Double Chance, Total Corners.  
  
Basketball options could include: Point Spread, Over/Under Total Points,  
Moneyline, Team Total Points.  
  
Format EXACTLY like this (no extra commentary outside the list):  
  
🏆 {team_a} vs {team_b}  
Sport detected: [Soccer/Basketball]  
{"📊 Using real recent form data" if real_stats else "⚠️ No live data found — general reasoning used"}  
  
1. [Market] — [Pick] — Reasoning in one short sentence  
2. [Market] — [Pick] — Reasoning in one short sentence  
3. [Market] — [Pick] — Reasoning in one short sentence  
4. [Market] — [Pick] — Reasoning in one short sentence  
5. [Market] — [Pick] — Reasoning in one short sentence  
  
⚠️ Bet responsibly. These are AI-generated suggestions, not guarantees.  
"""  
  
  
async def get_suggestions(team_a: str, team_b: str) -> str:  
    cache_key = f"{team_a.lower()}_vs_{team_b.lower()}"  
    if cache_key in cache:  
        return cache[cache_key]  
  
    real_stats = get_soccer_stats(team_a, team_b)  
    if not real_stats:  
        real_stats = get_basketball_stats(team_a, team_b)  
  
    prompt = build_prompt(team_a, team_b, real_stats)  
    response = client.chat.completions.create(  
        model="llama-3.3-70b-versatile",  
        messages=[{"role": "user", "content": prompt}],  
        temperature=0.7,  
        max_tokens=650,  
    )  
    result = response.choices[0].message.content  
    cache[cache_key] = result  
    return result  
  
  
# ---------- TELEGRAM HANDLERS ----------  
  
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):  
    await update.message.reply_text(  
        "👋 Send me a match like:\n\n"  
        "`Arsenal vs Chelsea`\n"  
        "`Lakers vs Celtics`\n\n"  
        "I'll pull this season's real form data (when available) and give you "  
        "5 smart betting suggestions.",  
        parse_mode="Markdown",  
    )  
  
  
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):  
    text = update.message.text.strip()  
    match = MATCH_PATTERN.match(text)  
  
    if not match:  
        await update.message.reply_text(  
            "Please send it like: `Team A vs Team B`", parse_mode="Markdown"  
        )  
        return  
  
    team_a, team_b = match.group(1).strip(), match.group(2).strip()  
    await update.message.reply_text(f"🔎 Checking stats & analyzing {team_a} vs {team_b}...")  
  
    try:  
        result = await get_suggestions(team_a, team_b)  
        await update.message.reply_text(result)  
    except Exception as e:  
        logging.error(f"Error: {e}")  
        await update.message.reply_text("⚠️ Something went wrong. Try again in a moment.")  
  
  
def main():  
    app = Application.builder().token(BOT_TOKEN).build()  
    app.add_handler(CommandHandler("start", start))  
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))  
    app.run_polling()  
  
  
if __name__ == "__main__":  
    main()  
