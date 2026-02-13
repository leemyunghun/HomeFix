import os
import json
import pandas as pd
import mysql.connector
from mysql.connector import Error
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime
from openai import OpenAI
from supabase import create_client
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import base64
from io import BytesIO

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

# ✅ TiDB 연결 설정 (사용자님 환경에 맞게 수정됨)
TIDB_CONFIG = {
    'host': os.getenv("TIDB_HOST", 'gateway01.ap-northeast-1.prod.aws.tidbcloud.com'),
    'port': 4000,
    'user': os.getenv("TIDB_USER", '4A4MKp4vECtWP3q.root'),
    'password': os.getenv("TIDB_PASSWORD", 'RS1QM9HXpZkb5Jyp'),
    'database': os.getenv("TIDB_DB_NAME", 'test'),
    'ssl_verify_cert': False
}

print(f"🚀 HomeFix 서비스 가동 중... 카카오 키: {'로드 완료' if KAKAO_JS_KEY else '미설정'}")

def get_db_connection():
    try:
        conn = mysql.connector.connect(**TIDB_CONFIG)
        return conn
    except Error as e:
        print(f"❌ TiDB 연결 오류: {e}")
        return None

# --- [유틸리티 함수] ---

def format_time_ago(db_time):
    try:
        # TiDB는 datetime 객체로 반환할 수 있음
        if isinstance(db_time, datetime):
            dt = db_time
        elif '.' in str(db_time):
            dt = datetime.strptime(str(db_time), '%Y-%m-%d %H:%M:%S.%f')
        else:
            dt = datetime.strptime(str(db_time), '%Y-%m-%d %H:%M:%S')

        # UTC 기준 비교
        now = datetime.utcnow()
        # 만약 DB 시간이 KST라면 9시간 조정이 필요할 수 있으나, 기존 로직 유지
        diff = now - dt
        seconds = diff.total_seconds()
        
        if seconds < 0: seconds = 0 # 미래 시간 방지
        
        if seconds < 60: return "방금 전"
        if seconds < 3600: return f"{int(seconds // 60)}분 전"
        if seconds < 86400: return f"{int(seconds // 3600)}시간 전"
        return f"{int(seconds // 86400)}일 전"
    except Exception as e:
        return "최근"

def sync_review_to_ai(review_data):
    knowledge_text = (
        f"실제 사용자 수리 후기: {review_data['contractor']} 업체에서 {review_data['item']} 수리를 진행함. "
        f"지불 비용은 {review_data['cost']}원이며, 사용자 평점은 5점 만점에 {review_data['rating']}점입니다. "
        f"사용자 상세 의견: {review_data['comment']}"
    )
    try:
        res = client.embeddings.create(input=knowledge_text, model="text-embedding-3-small")
        embedding = res.data[0].embedding
        # TiDB용 테이블명인 homefix_knowledge(또는 manual_data) 사용
        supabase.table("homefix_knowledge").insert({
            "content": knowledge_text,
            "metadata": {
                "source": "user_review", 
                "contractor": review_data['contractor'], 
                "rating": review_data['rating']
            },
            "embedding": embedding
        }).execute()
        print(f"✅ AI 학습 성공: {review_data['contractor']} 데이터 반영됨")
    except Exception as e:
        print(f"❌ AI 학습 전송 실패: {e}")

def encode_image(image_file):
    if not image_file or image_file.filename == '':
        return None
    try:
        image_file.seek(0)
        return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        print(f"❌ 이미지 인코딩 실패: {e}")
        return None

# --- [DB 초기화 함수] ---
def init_db():
    conn = get_db_connection()
    if not conn: return
    
    try:
        cursor = conn.cursor()
        
        # 1. 테이블 생성 (SQLite -> MySQL 문법 변환: AUTOINCREMENT -> AUTO_INCREMENT)
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255), userid VARCHAR(255) UNIQUE, 
            password VARCHAR(255), email VARCHAR(255), birthdate VARCHAR(255), phone VARCHAR(255))''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS history (
            id INT AUTO_INCREMENT PRIMARY KEY, userid VARCHAR(255), problem_name VARCHAR(255), 
            steps TEXT, tools TEXT, estimated_cost VARCHAR(255), 
            risk_level INT DEFAULT 1, warning TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS resources (
            id INT AUTO_INCREMENT PRIMARY KEY, category VARCHAR(50), name VARCHAR(255), 
            location TEXT, contact VARCHAR(255), link TEXT, description TEXT, lat REAL, lon REAL)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS support (
            id INT AUTO_INCREMENT PRIMARY KEY, userid VARCHAR(255), title VARCHAR(255), content TEXT, 
            status VARCHAR(50) DEFAULT '접수완료', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS reviews (
            id INT AUTO_INCREMENT PRIMARY KEY, userid VARCHAR(255), contractor_name VARCHAR(255), 
            repair_item VARCHAR(255), cost INT, rating INT, comment TEXT, 
            image_path TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # history 테이블 스키마 보정 (risk_level, warning 없을 경우 대비)
        try:
            cursor.execute("ALTER TABLE history ADD COLUMN risk_level INT DEFAULT 1")
            cursor.execute("ALTER TABLE history ADD COLUMN warning TEXT")
        except: pass

        conn.commit()

        # 2. 데이터 자동 로드 (CSV/JSON)
        cursor.execute("SELECT COUNT(*) as cnt FROM resources")
        cnt = cursor.fetchone()[0]
        
        if cnt == 0:
            print("🚀 데이터가 비어있어 파일 로드를 시도합니다...")
            
            # (1) 업체 CSV 로드
            contractor_path = os.path.join('homefix', '서울시 집수리 시공업체 정보.csv')
            if os.path.exists(contractor_path):
                try:
                    for enc in ['cp949', 'utf-8-sig', 'utf-8']:
                        try:
                            df = pd.read_csv(contractor_path, encoding=enc)
                            for _, row in df.iterrows():
                                cursor.execute(
                                    'INSERT INTO resources (category, name, location, contact, description) VALUES (%s, %s, %s, %s, %s)',
                                    ('expert', str(row['업체명']), str(row['업체주소']), str(row['업체연락처']), f"시공분야: {row['주요시공분야']}")
                                )
                            print(f"✅ 업체 데이터 {len(df)}건 로드 성공")
                            break
                        except: continue
                except Exception as e: print(f"❌ 업체 로드 실패: {e}")

            # (2) 대여소 JSON 로드
            json_path = 'homefix_data_final.json'
            if os.path.exists(json_path):
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        for item in data:
                            meta = item.get('metadata', {})
                            if meta.get('category') == 'expert': continue
                            if '대여' in meta.get('source', ''):
                                cursor.execute(
                                    'INSERT INTO resources (category, name, location, contact, description) VALUES (%s, %s, %s, %s, %s)',
                                    ('대여소', meta.get('name', '대여소'), meta.get('location', ''), meta.get('contact', ''), item.get('content', ''))
                                )
                    print("✅ 대여소 데이터 로드 성공")
                except Exception as e: print(f"❌ JSON 로드 실패: {e}")
            
            conn.commit()
    
    except Error as e:
        print(f"❌ DB 초기화 오류: {e}")
    finally:
        cursor.close()
        conn.close()
        print("✨ TiDB 준비 완료!")

init_db()

@app.context_processor
def inject_user():
    return dict(user_info=session.get('user'))

# --- [리뷰 관련 라우트] ---

@app.route('/review')
def review_page():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM reviews ORDER BY created_at DESC')
    reviews = cursor.fetchall()
    conn.close()
    return render_template('review.html', reviews=reviews)

@app.route('/add_review', methods=['POST'])
def add_review():
    if 'user' not in session:
        return "<script>alert('로그인이 필요합니다.'); history.back();</script>"
    
    contractor = request.form.get('contractor_name')
    item = request.form.get('repair_item')
    cost = request.form.get('cost')
    rating = request.form.get('rating')
    comment = request.form.get('comment')
    image_file = request.files.get('image')
    
    filename = ""
    if image_file and image_file.filename != '':
        from werkzeug.utils import secure_filename
        filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{image_file.filename}")
        upload_path = os.path.join('static', 'uploads', 'reviews')
        if not os.path.exists(upload_path):
            os.makedirs(upload_path)
        image_file.save(os.path.join(upload_path, filename))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''INSERT INTO reviews (userid, contractor_name, repair_item, cost, rating, comment, image_path)
                      VALUES (%s, %s, %s, %s, %s, %s, %s)''', 
                   (session['user']['userid'], contractor, item, cost, rating, comment, filename))
    conn.commit()
    conn.close()
    
    sync_review_to_ai({
        'contractor': contractor, 'item': item, 'cost': cost, 
        'rating': rating, 'comment': comment
    })
    
    return redirect(url_for('review_page'))

# --- [메인 & 검색 라우트] ---

@app.route('/')
def index():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # 1. 통계 데이터
    cursor.execute('SELECT AVG(cost) as avg_cost FROM reviews')
    avg_row = cursor.fetchone()
    avg_price = avg_row['avg_cost'] if avg_row and avg_row['avg_cost'] else 0
    
    cursor.execute('SELECT COUNT(*) as cnt FROM reviews')
    total_knowledge = cursor.fetchone()['cnt'] + 1240
    
    # 2. 실시간 수리 내역
    cursor.execute('SELECT problem_name, created_at FROM history ORDER BY created_at DESC LIMIT 10')
    recent_rows = cursor.fetchall()
    conn.close()

    recent_fixes = []
    for row in recent_rows:
        recent_fixes.append({
            "problem_name": row['problem_name'],
            "time_ago": format_time_ago(row['created_at']),
            "status": "진단 완료"
        })

    stats = {
        "avg_price": f"{int(avg_price):,}",
        "total_knowledge": total_knowledge
    }
    
    return render_template('index.html', stats=stats, recent_fixes=recent_fixes)

@app.route('/rental')
def rental_page():
    query = request.args.get('query', '').strip()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    rentals = []

    try:
        sql = "SELECT * FROM resources WHERE category = '대여소'"
        params = []
        
        if query:
            search_term = f"%{query}%"
            # TiDB(MySQL)에서는 LIKE %s 사용
            sql += " AND (location LIKE %s OR name LIKE %s OR description LIKE %s)"
            params = [search_term, search_term, search_term]
            cursor.execute(sql, params)
        else:
            cursor.execute(sql + " LIMIT 15")
        
        rentals = cursor.fetchall()
    except Exception as e:
        print(f"❌ 대여소 검색 중 오류 발생: {e}")
    finally:
        cursor.close()
        conn.close()

    return render_template('rental.html', rentals=rentals, query=query, kakao_js_key=KAKAO_JS_KEY)

@app.route('/expert')
def expert_matching():
    query = request.args.get('query', '').strip()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    experts = []
    
    try:
        sql = "SELECT * FROM resources WHERE category = 'expert'"
        
        if query:
            search_term = f"%{query}%"
            sql += " AND (name LIKE %s OR location LIKE %s OR description LIKE %s) ORDER BY name ASC"
            cursor.execute(sql, [search_term, search_term, search_term])
        else:
            cursor.execute(sql + " LIMIT 15")
            
        experts = cursor.fetchall()
    except Exception as e:
        print(f"❌ 업체 DB 조회 오류: {e}")
    finally:
        cursor.close()
        conn.close()

    return render_template('expert.html', experts=experts, query=query, kakao_js_key=KAKAO_JS_KEY)

# --- [AI 진단 라우트] ---

@app.route('/diagnose', methods=['POST'])
def diagnose():
    if 'user' not in session:
        return jsonify({"status": "invalid", "message": "로그인이 필요합니다."}), 401

    user_input = request.form.get('problem', '').strip()
    image_file = request.files.get('image')
    has_image = image_file and image_file.filename != ''

    if not user_input and not has_image:
        return jsonify({"status": "invalid", "message": "증상을 설명하시거나 사진을 첨부해주세요."}), 400

    try:
        # 1. 텍스트 RAG 검색
        context = "관련 매뉴얼 내용을 찾을 수 없습니다."
        sources = ["일반 지식"]
        
        if user_input:
            emb_res = client.embeddings.create(input=user_input.replace("\n", " "), model="text-embedding-3-small")
            query_embedding = emb_res.data[0].embedding
            rpc_res = supabase.rpc("match_documents", {
                "query_embedding": query_embedding,
                "match_threshold": 0.25,
                "match_count": 6
            }).execute()
            
            if rpc_res.data:
                documents = rpc_res.data
                sources = list(set([doc.get('metadata', {}).get('source', '일반 지식') for doc in documents]))
                context = "\n\n".join([f"[{doc['metadata'].get('source')}] {doc['content']}" for doc in documents])

        ai_query = user_input if user_input else "사진 속의 집수리 문제를 분석하고 해결책을 제시해줘."

        # 2. GPT 프롬프트 구성
        text_content = f"""당신은 집수리 전문가 '홈픽스 삼촌'입니다. 
        사용자가 제공한 정보(텍스트/사진)를 바탕으로 전문적인 진단을 내려주세요.
        
        [학습 지식 베이스]
        {context}

        [사용자 입력]
        {ai_query}

        [답변 형식 JSON]
        {{
          "problem_name": "제목",
          "risk_level": 1~5,
          "estimated_cost": "예상 비용",
          "warning": "안전 주의사항",
          "steps": ["구체적 단계"],
          "tools": ["필요 도구"],
          "sources": "{', '.join(sources)}"
        }}"""

        messages_content = [{"type": "text", "text": text_content}]

        if has_image:
            base64_image = encode_image(image_file)
            if base64_image:
                messages_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                })

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": messages_content}],
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        
        # 🛡️ 방어 로직 (risk_level 누락 방지)
        result['risk_level'] = result.get('risk_level', 1)
        result['warning'] = result.get('warning', '주의사항 없음')

        # 3. DB 저장
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO history (userid, problem_name, steps, tools, estimated_cost, risk_level, warning) VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (session['user']['userid'], result.get('problem_name', '사진 진단'), 
            json.dumps(result.get('steps', []), ensure_ascii=False),
            json.dumps(result.get('tools', []), ensure_ascii=False),
            result.get('estimated_cost', '비용 정보 없음'),
            result['risk_level'],
            result['warning'])
        )
        conn.commit()
        cursor.close()
        conn.close()
        
        session['last_result'] = result
        return jsonify(result)

    except Exception as e:
        print(f"❌ 진단 프로세스 오류: {e}")
        return jsonify({"status": "error", "message": "진단 중 오류가 발생했습니다."}), 500

# --- [계정 관리 및 기타 라우트] ---

@app.route('/history/<int:history_id>')
def history_detail(history_id):
    if 'user' not in session: return redirect('/login')
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM history WHERE id = %s AND userid = %s', (history_id, session['user']['userid']))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if row is None: return "<script>alert('내역을 찾을 수 없습니다.'); history.back();</script>"
    
    result_data = {
        "problem_name": row['problem_name'],
        "steps": json.loads(row['steps']),
        "tools": json.loads(row['tools']),
        "estimated_cost": row['estimated_cost'] if row['estimated_cost'] else "비용 정보 없음",
        "risk_level": row['risk_level'], 
        "warning": row['warning']
    }
    return render_template('result.html', result_data=result_data)

@app.route('/check_id', methods=['POST'])
def check_id():
    data = request.get_json()
    uid = data.get('userid')
    if not uid: return jsonify({"result": "error", "message": "아이디를 입력해주세요."})
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM users WHERE userid = %s', (uid,))
    user = cursor.fetchone()
    cursor.close(); conn.close()
    
    if user: return jsonify({"result": "exists", "message": "이미 사용 중인 아이디입니다."})
    return jsonify({"result": "success", "message": "사용 가능한 아이디입니다."})

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        uid, pw = request.form.get('userid'), request.form.get('password')
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM users WHERE userid = %s', (uid,))
        user = cursor.fetchone()
        cursor.close(); conn.close()
        
        if user and check_password_hash(user['password'], pw):
            session['user'] = user
            return redirect(url_for('index'))
        return "<script>alert('틀린 정보입니다.'); history.back();</script>"
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        d = request.form
        hashed_pw = generate_password_hash(d['password'])
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('INSERT INTO users (name, userid, password, email, birthdate, phone) VALUES (%s, %s, %s, %s, %s, %s)',
                           (d['name'], d['userid'], hashed_pw, d['email'], d['birthdate'], d['phone']))
            conn.commit()
            return "<script>alert('가입 성공!'); location.href='/login';</script>"
        except Exception as e:
            return f"<script>alert('오류 발생: {e}'); history.back();</script>"
        finally:
            cursor.close(); conn.close()
    return render_template('signup.html')

@app.route('/find-id', methods=['GET', 'POST'])
def find_id():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT userid FROM users WHERE name = %s AND email = %s', (name, email))
        user = cursor.fetchone()
        cursor.close(); conn.close()
        
        if user:
            return f"<script>alert('회원님의 아이디는 [{user['userid']}] 입니다.'); location.href='/login';</script>"
        else:
            return "<script>alert('일치하는 정보가 없습니다.'); history.back();</script>"
    return render_template('find_id.html')

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        uid = request.form.get('userid')
        current_pw = request.form.get('current_password')
        email = request.form.get('email')
        new_pw = request.form.get('new_password')
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM users WHERE userid = %s AND email = %s', (uid, email))
        user = cursor.fetchone()
        
        if user and check_password_hash(user['password'], current_pw):
            hashed_pw = generate_password_hash(new_pw)
            # cursor를 새로 열거나 기존 cursor 재사용 (Update용)
            cursor.execute('UPDATE users SET password = %s WHERE id = %s', (hashed_pw, user['id']))
            conn.commit()
            cursor.close(); conn.close()
            return "<script>alert('비밀번호가 성공적으로 변경되었습니다.'); location.href='/login';</script>"
        else:
            cursor.close(); conn.close()
            return "<script>alert('정보 불일치'); history.back();</script>"
    return render_template('reset_password.html')

@app.route('/change_password', methods=['POST'])
def change_password():
    if 'user' not in session: return redirect('/login')

    current_pw = request.form.get('current_password')
    new_pw = request.form.get('new_password')
    confirm_pw = request.form.get('confirm_password')
    userid = session['user']['userid']

    if new_pw != confirm_pw:
        return "<script>alert('새 비밀번호 확인이 일치하지 않습니다.'); history.back();</script>"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM users WHERE userid = %s', (userid,))
    user = cursor.fetchone()

    if user and check_password_hash(user['password'], current_pw):
        new_hashed_pw = generate_password_hash(new_pw)
        cursor.execute('UPDATE users SET password = %s WHERE userid = %s', (new_hashed_pw, userid))
        conn.commit()
        cursor.close(); conn.close()
        session.clear()
        return "<script>alert('비밀번호가 변경되었습니다. 다시 로그인해주세요.'); location.href='/login';</script>"
    else:
        cursor.close(); conn.close()
        return "<script>alert('현재 비밀번호가 틀렸습니다.'); history.back();</script>"

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

@app.route('/result')
def result_page():
    res = session.get('last_result')
    if not res: return redirect('/')
    # 안전한 딕셔너리 접근을 위해 처리
    safe_data = {
        'problem_name': res.get('problem_name', '진단 결과'),
        'steps': res.get('steps', []),
        'tools': res.get('tools', []),
        'estimated_cost': res.get('estimated_cost', '비용 정보 없음'),
        'risk_level': res.get('risk_level', 1),
        'warning': res.get('warning', '주의사항 없음')
    }
    return render_template('result.html', result_data=safe_data)

@app.route('/myinfo')
def myinfo():
    if 'user' not in session: return redirect('/login')
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM history WHERE userid = %s ORDER BY created_at DESC', (session['user']['userid'],))
    history = cursor.fetchall()
    cursor.close(); conn.close()
    return render_template('myinfo.html', history=history)

@app.route('/delete_history/<int:history_id>', methods=['POST'])
def delete_history(history_id):
    if 'user' not in session: return jsonify({'status':'error'}), 401
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM history WHERE id = %s AND userid = %s', (history_id, session['user']['userid']))
    conn.commit()
    cursor.close(); conn.close()
    return jsonify({"status": "success"})

@app.route('/support', methods=['GET', 'POST'])
def support():
    if request.method == 'POST':
        if 'user' not in session: return redirect('/login')
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO support (userid, title, content) VALUES (%s, %s, %s)', 
                       (session['user']['userid'], request.form.get('title'), request.form.get('content')))
        conn.commit()
        cursor.close(); conn.close()
        return redirect('/support/my')
    return render_template('support.html')

@app.route('/support/my')
def support_my():
    if 'user' not in session: return redirect('/login')
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM support WHERE userid = %s ORDER BY created_at DESC', (session['user']['userid'],))
    my_supports = cursor.fetchall()
    cursor.close(); conn.close()
    return render_template('support_my.html', supports=my_supports)

@app.route('/delete_account', methods=['POST'])
def delete_account():
    user_data = session.get('user')
    if not user_data: return "Unauthorized", 401

    target_id = user_data.get('id')
    user_uid = user_data.get('userid')

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM users WHERE id = %s', (target_id,))
        cursor.execute('DELETE FROM history WHERE userid = %s', (user_uid,))
        cursor.execute('DELETE FROM support WHERE userid = %s', (user_uid,))
        cursor.execute('DELETE FROM reviews WHERE userid = %s', (user_uid,)) # 리뷰 삭제 추가 권장
        conn.commit()
        session.clear()
        return "Success", 200
    except Exception as e:
        print(f"❌ 회원탈퇴 오류: {e}")
        return "Error", 500
    finally:
        cursor.close(); conn.close()

@app.route('/terms')
def terms(): return render_template('terms.html')

@app.route('/privacy')
def privacy(): return render_template('privacy.html')

if __name__ == '__main__':
    app.run(debug=True)