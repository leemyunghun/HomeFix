import os
import json
import sqlite3
import math
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from openai import OpenAI
from supabase import create_client
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

# .env 파일 로드
load_dotenv()

app = Flask(__name__)

# 보안 및 API 설정
app.secret_key = os.getenv("FLASK_SECRET_KEY", "home_fix_fallback_key")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ✅ Supabase 설정
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ✅ 카카오 JavaScript 키
KAKAO_JS_KEY = os.getenv("KAKAO_JS_KEY", "")

print(f"🚀 HomeFix 서비스 가동 중... 카카오 키: {'로드 완료' if KAKAO_JS_KEY else '미설정'}")

def init_db():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    
    # 1. 사용자 테이블
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  name TEXT, userid TEXT UNIQUE, password TEXT, 
                  email TEXT, birthdate TEXT, phone TEXT)''')
    
    # 2. 수리 내역 테이블
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  userid TEXT, problem_name TEXT, steps TEXT, tools TEXT,
                  estimated_cost TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # 3. 공구 대여소 및 업체 테이블 (lat, lon 컬럼 포함)
    c.execute('''CREATE TABLE IF NOT EXISTS resources
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  category TEXT, name TEXT, location TEXT, contact TEXT, 
                  link TEXT, description TEXT, lat REAL, lon REAL)''')
    
    # 4. 고객지원 테이블
    c.execute('''CREATE TABLE IF NOT EXISTS support
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  userid TEXT, title TEXT, content TEXT,
                  status TEXT DEFAULT '접수완료',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    # ✅ 마이그레이션: 기존 DB에 컬럼이 없는 경우 추가
    try:
        c.execute('ALTER TABLE resources ADD COLUMN lat REAL')
    except sqlite3.OperationalError: pass
        
    try:
        c.execute('ALTER TABLE resources ADD COLUMN lon REAL')
    except sqlite3.OperationalError: pass

    try:
        c.execute('ALTER TABLE history ADD COLUMN estimated_cost TEXT')
    except sqlite3.OperationalError: pass

    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.context_processor
def inject_user():
    return dict(user_info=session.get('user'))

# --- 라우트 정의 ---

@app.route('/')
def index():
    conn = get_db_connection()
    recent_fixes = conn.execute('SELECT problem_name FROM history ORDER BY created_at DESC LIMIT 5').fetchall()
    history_count = conn.execute('SELECT COUNT(*) FROM history').fetchone()[0]
    conn.close()
    
    stats = {
        "total_guides": history_count + 1240,
        "total_rentals": "실시간", 
        "popular_tool": "전동 드릴",
        "speed": "1.2s",
        "satisfaction": "98%"
    }
    return render_template('index.html', recent_fixes=recent_fixes, stats=stats)

@app.route('/rental')
def rental_page():
    # 주소창에 ?query=성북구 처럼 검색어가 들어올 때 처리
    query = request.args.get('query', type=str)
    
    conn = get_db_connection()
    rentals = []

    if query and query.strip():
        # ✅ 사용자가 입력한 검색어로 9개 CSV 데이터(resources 테이블)에서만 검색
        search_term = f"%{query.strip()}%"
        rentals_raw = conn.execute('''
            SELECT * FROM resources 
            WHERE (location LIKE ? OR name LIKE ? OR description LIKE ?)
        ''', (search_term, search_term, search_term)).fetchall()
        rentals = [dict(r) for r in rentals_raw]
    else:
        # 검색어가 없을 때는 빈 리스트 혹은 기본 데이터 10개 노출
        rentals_raw = conn.execute("SELECT * FROM resources LIMIT 10").fetchall()
        rentals = [dict(r) for r in rentals_raw]

    conn.close()
    # 템플릿에 데이터와 카카오 키 전달
    return render_template('rental.html', rentals=rentals, kakao_js_key=KAKAO_JS_KEY)

# ✅ 하이브리드 RAG 진단 함수
@app.route('/diagnose', methods=['POST'])
def diagnose():
    if 'user' not in session:
        return jsonify({"status": "invalid", "message": "로그인이 필요합니다."}), 401

    user_input = request.form.get('problem', '')

    try:
        emb_res = client.embeddings.create(input=user_input.replace("\n", " "), model="text-embedding-3-small")
        query_embedding = emb_res.data[0].embedding

        rpc_res = supabase.rpc("match_documents", {
            "query_embedding": query_embedding,
            "match_threshold": 0.3,
            "match_count": 5
        }).execute()

        documents = rpc_res.data
        sources = list(set([doc.get('metadata', {}).get('source', '일반 지식') for doc in documents])) if documents else ["일반 상식"]
        context = "\n\n".join([doc['content'] for doc in documents]) if documents else "검색된 특정 매뉴얼 정보가 없습니다."

        prompt = f"""당신은 이웃집 수리 고수 '홈픽스 삼촌'입니다. 
사용자가 전문 용어를 전혀 모른다고 가정하고, 아주 쉬운 '옆집 사람 말투'로 설명하세요.

[필수 지침]
1. 어려운 용어 금지: 이해하기 쉽게 풀어서 설명하세요.
2. 비용의 투명성: 직접 할 때와 사람 부를 때의 비용을 구체적으로 비교하세요.
3. RAG 지식 활용: 제공된 [전문 지식]을 참고하세요. (참고 출처: {", ".join(sources)})
4. 주제 제한: 집 수리 외의 질문은 정중히 거절하세요.

[전문 지식 참고]
{context}

[사용자 입력]
{user_input}

[답변 형식 JSON]
{{
  "problem_name": "제목",
  "risk_level": 1~5,
  "estimated_cost": "직접 할 때 0원 / 사람 부를 때 0원",
  "warning": "주의사항",
  "steps": ["1단계", "2단계", "..."],
  "tools": ["도구명(용도)"],
  "sources": "{", ".join(sources)}"
}}"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        
        conn = get_db_connection()
        conn.execute(
            'INSERT INTO history (userid, problem_name, steps, tools, estimated_cost) VALUES (?, ?, ?, ?, ?)',
            (session['user']['userid'], result['problem_name'], 
            json.dumps(result['steps'], ensure_ascii=False),
            json.dumps(result['tools'], ensure_ascii=False),
            result.get('estimated_cost', '비용 정보 없음'))
        )
        conn.commit()
        conn.close()
        
        session['last_result'] = result
        return jsonify(result)

    except Exception as e:
        print(f"❌ AI 진단 오류: {e}")
        return jsonify({"status": "error", "message": "진단 중 오류 발생"}), 500

# --- 공통 라우트 (회원가입, 로그인 등) ---

@app.route('/history/<int:history_id>')
def history_detail(history_id):
    if 'user' not in session: return redirect('/login')
    conn = get_db_connection()
    row = conn.execute('SELECT * FROM history WHERE id = ? AND userid = ?', (history_id, session['user']['userid'])).fetchone()
    conn.close()
    if row is None: return "<script>alert('내역을 찾을 수 없습니다.'); history.back();</script>"
    result_data = {
        "problem_name": row['problem_name'],
        "steps": json.loads(row['steps']),
        "tools": json.loads(row['tools']),
        "estimated_cost": row['estimated_cost'] if row['estimated_cost'] else "비용 정보 없음",
        "risk_level": 0, "warning": "과거 진단 내역입니다."
    }
    return render_template('result.html', result_data=result_data)

@app.route('/check_id', methods=['POST'])
def check_id():
    data = request.get_json()
    user = get_db_connection().execute('SELECT * FROM users WHERE userid = ?', (data.get('userid'),)).fetchone()
    if user: return jsonify({"result": "exists", "message": "이미 사용 중인 아이디입니다."})
    return jsonify({"result": "success", "message": "사용 가능한 아이디입니다."})

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        uid, pw = request.form.get('userid'), request.form.get('password')
        user = get_db_connection().execute('SELECT * FROM users WHERE userid = ?', (uid,)).fetchone()
        if user and check_password_hash(user['password'], pw):
            session['user'] = dict(user)
            return redirect(url_for('index'))
        return "<script>alert('틀린 정보입니다.'); history.back();</script>"
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        d = request.form
        hashed_pw = generate_password_hash(d['password'])
        conn = get_db_connection()
        try:
            conn.execute('INSERT INTO users (name, userid, password, email, birthdate, phone) VALUES (?, ?, ?, ?, ?, ?)',
                        (d['name'], d['userid'], hashed_pw, d['email'], d['birthdate'], d['phone']))
            conn.commit()
            return "<script>alert('가입 성공!'); location.href='/login';</script>"
        except: return "<script>alert('이미 있는 아이디입니다.'); history.back();</script>"
        finally: conn.close()
    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

@app.route('/result')
def result_page():
    res = session.get('last_result')
    return render_template('result.html', result_data=res) if res else redirect('/')

@app.route('/myinfo')
def myinfo():
    if 'user' not in session: return redirect('/login')
    conn = get_db_connection()
    history = conn.execute('SELECT * FROM history WHERE userid = ? ORDER BY created_at DESC', (session['user']['userid'],)).fetchall()
    conn.close()
    return render_template('myinfo.html', history=history)

@app.route('/delete_history/<int:history_id>', methods=['POST'])
def delete_history(history_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM history WHERE id = ? AND userid = ?', (history_id, session['user']['userid']))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/support', methods=['GET', 'POST'])
def support():
    if request.method == 'POST':
        if 'user' not in session: return redirect('/login')
        conn = get_db_connection()
        conn.execute('INSERT INTO support (userid, title, content) VALUES (?, ?, ?)', (session['user']['userid'], request.form.get('title'), request.form.get('content')))
        conn.commit()
        conn.close()
        return redirect('/support/my')
    return render_template('support.html')
@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')
@app.route('/expert')
def expert_matching():
    return render_template('expert.html', kakao_js_key=KAKAO_JS_KEY)

@app.route('/support/my')
def support_my():
    if 'user' not in session: return redirect('/login')
    my_supports = get_db_connection().execute('SELECT * FROM support WHERE userid = ? ORDER BY created_at DESC', (session['user']['userid'],)).fetchall()
    return render_template('support_my.html', supports=my_supports)
@app.route('/delete_account', methods=['POST'])
def delete_account():
    # 1. 로그인 시 'user' 키로 저장했으므로 이를 꺼내옵니다.
    user_data = session.get('user')
    
    if not user_data:
        return "Unauthorized", 401

    # 세션에 저장된 유저의 고유 ID(pk)를 가져옵니다.
    target_id = user_data.get('id')

    conn = get_db_connection()
    try:
        # 2. DB에서 유저 삭제
        conn.execute('DELETE FROM users WHERE id = ?', (target_id,))
        
        # 해당 유저의 히스토리와 문의사항도 함께 삭제
        user_uid = user_data.get('userid')
        conn.execute('DELETE FROM history WHERE userid = ?', (user_uid,))
        conn.execute('DELETE FROM support WHERE userid = ?', (user_uid,))
        
        conn.commit()
        
        # 3. 모든 세션 비우기 (자동 로그아웃)
        session.clear() 
        print(f"✅ 회원탈퇴 성공: {user_uid}")
        return "Success", 200
    except Exception as e:
        print(f"❌ 회원탈퇴 오류: {e}")
        return "Error", 500
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=True)