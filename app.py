import os
import json
import pandas as pd
import mysql.connector
import uuid
from mysql.connector import Error
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_session import Session
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from openai import OpenAI
from supabase import create_client
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import base64
from io import BytesIO
import requests as http_requests  # 결제 API 호출용 (flask request와 충돌 방지)

# .env 파일 로드
load_dotenv()

# ✨ 통합: 기본 templates 폴더 하나만 사용합니다.
app = Flask(__name__)
# --- 세션 설정 추가 ---
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"  # 세션 데이터를 서버 파일로 저장
Session(app)    
# --------------------

app.secret_key = os.getenv("FLASK_SECRET_KEY", "home_fix_fallback_key")

# 보안 및 API 설정
app.secret_key = os.getenv("FLASK_SECRET_KEY", "home_fix_fallback_key")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Supabase 설정
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# 카카오 JavaScript 키
KAKAO_JS_KEY = os.getenv("KAKAO_JS_KEY", "")

# ✨ [결제 시스템] 추가 환경변수
# .env에 아래 항목을 추가해주세요:
#   KAKAO_ADMIN_KEY=발급받은_카카오_Admin_키
#   TOSS_SECRET_KEY=test_sk_...
#   TOSS_CLIENT_KEY=test_ck_...
#   BASE_URL=http://localhost:5000
KAKAO_ADMIN_KEY = os.getenv("KAKAO_ADMIN_KEY", "")
TOSS_SECRET_KEY = os.getenv("TOSS_SECRET_KEY", "")
TOSS_CLIENT_KEY = os.getenv("TOSS_CLIENT_KEY", "")
BASE_URL        = os.getenv("BASE_URL", "http://localhost:5000")

# TiDB 연결 설정
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
        # ✨ buffered=True 옵션을 추가합니다.
        conn = mysql.connector.connect(**TIDB_CONFIG, buffered=True)
        return conn
    except Error as e:
        print(f"❌ TiDB 연결 오류: {e}")
        return None

# --- [유틸리티 함수] ---
def format_time_ago(db_time):
    try:
        if isinstance(db_time, datetime):
            dt = db_time
        elif '.' in str(db_time):
            dt = datetime.strptime(str(db_time), '%Y-%m-%d %H:%M:%S.%f')
        else:
            dt = datetime.strptime(str(db_time), '%Y-%m-%d %H:%M:%S')

        now = datetime.utcnow()
        diff = now - dt
        seconds = diff.total_seconds()
        
        if seconds < 0: seconds = 0
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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reservations (
                id INT AUTO_INCREMENT PRIMARY KEY,
                userid VARCHAR(50),
                expert_name VARCHAR(100),
                used_points INT DEFAULT 0,
                status VARCHAR(20) DEFAULT '예약대기',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS point_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                userid VARCHAR(255),
                amount INT,
                description VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # ✨ [결제 시스템] 결제 내역 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                userid        VARCHAR(255) NOT NULL,
                order_id      VARCHAR(100) UNIQUE NOT NULL,
                payment_type  VARCHAR(50)  NOT NULL,
                purpose       VARCHAR(50)  NOT NULL,
                amount        INT          NOT NULL,
                point_amount  INT          DEFAULT 0,
                status        VARCHAR(30)  DEFAULT '대기',
                pg_tid        VARCHAR(200) DEFAULT '',
                ref_id        INT          DEFAULT NULL,
                created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        ''')

        # ✨ [결제 시스템] 구독/멤버십 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                userid       VARCHAR(255) UNIQUE NOT NULL,
                plan         VARCHAR(50)  DEFAULT 'free',
                start_date   DATE,
                end_date     DATE,
                status       VARCHAR(30)  DEFAULT 'inactive',
                created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        try:
            cursor.execute("ALTER TABLE history ADD COLUMN risk_level INT DEFAULT 1")
            cursor.execute("ALTER TABLE history ADD COLUMN warning TEXT")
        except: pass

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN points INT DEFAULT 0")
        except: pass

        conn.commit()

        cursor.execute("SELECT COUNT(*) as cnt FROM resources")
        cnt = cursor.fetchone()[0]
        if cnt == 0:
            print("🚀 데이터가 비어있어 파일 로드를 시도합니다...")
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

# ==========================================
# [1] 메인, 후기, 전문가 등 일반 유저 라우트
# ==========================================

@app.route('/')
def index():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT AVG(cost) as avg_cost FROM reviews')
    avg_row = cursor.fetchone()
    avg_price = avg_row['avg_cost'] if avg_row and avg_row['avg_cost'] else 0
    cursor.execute('SELECT COUNT(*) as cnt FROM reviews')
    total_knowledge = cursor.fetchone()['cnt'] + 1240
    cursor.execute('SELECT problem_name, created_at FROM history ORDER BY created_at DESC LIMIT 10')
    recent_rows = cursor.fetchall()
    conn.close()

    recent_fixes = [{"problem_name": r['problem_name'], "time_ago": format_time_ago(r['created_at']), "status": "진단 완료"} for r in recent_rows]
    stats = {"avg_price": f"{int(avg_price):,}", "total_knowledge": total_knowledge}
    return render_template('index.html', stats=stats, recent_fixes=recent_fixes)

@app.before_request
def update_last_seen():
    """로그인한 사용자가 페이지를 이동할 때마다 마지막 활동 시간을 기록합니다."""
    if 'user' in session:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET last_seen = NOW() WHERE userid = %s", (session['user']['userid'],))
            conn.commit()
            cursor.close(); conn.close()
        except Exception as e:
            print(f"활동 시간 기록 오류: {e}")

@app.route('/review')
def review_page():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM reviews ORDER BY created_at DESC')
    reviews = cursor.fetchall()
    conn.close()

    for r in reviews:
        if r.get('created_at'):
            if isinstance(r['created_at'], datetime):
                r['created_at'] = r['created_at'].strftime('%Y-%m-%d')
            else:
                r['created_at'] = str(r['created_at'])[:10]

    return render_template('review.html', reviews=reviews)

@app.route('/add_review', methods=['POST'])
def add_review():
    if 'user' not in session: return "<script>alert('로그인이 필요합니다.'); history.back();</script>"
    
    contractor = request.form.get('contractor_name')
    item = request.form.get('repair_item')
    cost = request.form.get('cost')
    rating = request.form.get('rating')
    comment = request.form.get('comment')
    image_file = request.files.get('image')
    
    filename = ""
    if image_file and image_file.filename != '':
        filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{image_file.filename}")
        upload_path = os.path.join('static', 'uploads', 'reviews')
        if not os.path.exists(upload_path): os.makedirs(upload_path)
        image_file.save(os.path.join(upload_path, filename))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # ✨ 버그 수정: reward_points 값을 먼저 선언해야 INSERT 시 오류가 나지 않습니다.
    reward_points = 1000
    
    # 1. 리뷰 데이터 저장
    cursor.execute('''INSERT INTO reviews (userid, contractor_name, repair_item, cost, rating, comment, image_path)
                      VALUES (%s, %s, %s, %s, %s, %s, %s)''', 
                   (session['user']['userid'], contractor, item, cost, rating, comment, filename))
                   
    # 2. 포인트 내역 저장
    cursor.execute("INSERT INTO point_history (userid, amount, description) VALUES (%s, %s, %s)",
                   (session['user']['userid'], reward_points, "리뷰 작성 보상"))
    
    # 3. 유저 포인트 업데이트
    cursor.execute('UPDATE users SET points = points + %s WHERE userid = %s', (reward_points, session['user']['userid']))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    # 4. 화면에 바로 반영되도록 현재 로그인 세션 정보 업데이트
    if 'points' in session['user']:
        session['user']['points'] += reward_points
    else:
        session['user']['points'] = reward_points
    session.modified = True
    
    # 5. AI 학습 진행
    try:
        sync_review_to_ai({'contractor': contractor, 'item': item, 'cost': cost, 'rating': rating, 'comment': comment})
    except Exception as e: print(f"⚠️ AI 학습 실패: {e}")
    
    return f"<script>alert('소중한 후기가 등록되어 {reward_points} 포인트가 지급되었습니다!'); location.href='/review';</script>"

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
            sql += " AND (location LIKE %s OR name LIKE %s OR description LIKE %s)"
            params = [search_term, search_term, search_term]
            cursor.execute(sql, params)
        else:
            cursor.execute(sql + "ORDER BY RAND() LIMIT 3")
        rentals = cursor.fetchall()
    finally:
        cursor.close(); conn.close()
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
            cursor.execute(sql + " ORDER BY RAND() LIMIT 3")
        raw_experts = cursor.fetchall()
        
        seen_names = set()
        for expert in raw_experts:
            if expert['name'] not in seen_names:
                experts.append(expert)
                seen_names.add(expert['name'])
    finally:
        cursor.close(); conn.close()
    return render_template('expert.html', experts=experts, query=query, kakao_js_key=KAKAO_JS_KEY)

@app.route('/reserve_expert', methods=['POST'])
def reserve_expert():
    if 'user' not in session: return "<script>alert('로그인이 필요합니다.'); location.href='/login';</script>"
    
    expert_name = request.form.get('expert_name')
    raw_points = request.form.get('use_points', '0')
    use_points = int(raw_points) if raw_points.isdigit() else 0
    current_points = session['user'].get('points', 0)
    
    if use_points > current_points:
        return "<script>alert('보유 포인트가 부족합니다.'); history.back();</script>"
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. 예약 내역 저장
    cursor.execute("INSERT INTO reservations (userid, expert_name, used_points) VALUES (%s, %s, %s)", 
                   (session['user']['userid'], expert_name, use_points))
    
    # 2. 포인트 차감
    cursor.execute("UPDATE users SET points = points - %s WHERE userid = %s", (use_points, session['user']['userid']))
    
    # 3. 포인트 사용 내역 기록
    if use_points > 0:
        cursor.execute("INSERT INTO point_history (userid, amount, description) VALUES (%s, %s, %s)",
                       (session['user']['userid'], -use_points, f"{expert_name} 수리 예약에 사용"))
    conn.commit()
    cursor.close(); conn.close()
    
    session['user']['points'] -= use_points
    session.modified = True
    
    return "<script>alert('포인트를 사용하여 할인이 적용된 예약이 접수되었습니다!'); location.href='/myinfo';</script>"

# --- 공지사항 ---
@app.route('/notice')
def notice_list():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM notice ORDER BY created_at DESC")
        notices = cursor.fetchall()
        for n in notices:
            if n.get('created_at'):
                if isinstance(n['created_at'], datetime): n['created_at'] = n['created_at'].strftime('%Y-%m-%d')
                else: n['created_at'] = str(n['created_at'])[:10]
    finally:
        cursor.close(); conn.close()
    return render_template('notice.html', notices=notices)

@app.route('/notice/<int:notice_id>')
def notice_detail(notice_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        viewed_notices = session.get('viewed_notices', [])
        if notice_id not in viewed_notices:
            cursor.execute("UPDATE notice SET views = views + 1 WHERE id = %s", (notice_id,))
            conn.commit()
            viewed_notices.append(notice_id)
            session['viewed_notices'] = viewed_notices

        cursor.execute("SELECT * FROM notice WHERE id = %s", (notice_id,))
        notice_data = cursor.fetchone()
        if notice_data and isinstance(notice_data['created_at'], datetime):
            notice_data['created_at'] = notice_data['created_at'].strftime('%Y-%m-%d %H:%M')
    finally:
        cursor.close(); conn.close()
    return render_template('notice_detail.html', notice=notice_data, is_edit=False)

@app.route('/notice/write', methods=['GET', 'POST'])
def notice_write():
    user_info = session.get('user')
    current_id = user_info.get('userid', '') if user_info and isinstance(user_info, dict) else session.get('userid')
    if not current_id: return "<script>alert('로그인이 필요합니다.'); location.href='/login';</script>"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT role FROM users WHERE userid = %s", (current_id,))
        user_data = cursor.fetchone()
        if not user_data or user_data.get('role') != 'admin':
            return "<script>alert('관리자 권한이 없습니다.'); history.back();</script>"
            
        if request.method == 'POST':
            title = request.form.get('title')
            content = request.form.get('content')
            if not title or not content: return "<script>alert('제목과 내용을 모두 입력해주세요.'); history.back();</script>"
            cursor.execute("INSERT INTO notice (title, content, author) VALUES (%s, %s, %s)", (title, content, '관리자'))
            conn.commit()
            return redirect(url_for('notice_list'))
    finally:
        cursor.close(); conn.close()
    return render_template('notice_write.html', is_edit=False)

@app.route('/notice/edit/<int:notice_id>', methods=['GET', 'POST'])
def notice_edit(notice_id):
    if session.get('user', {}).get('role') != 'admin': return "<script>alert('관리자 권한이 없습니다.'); history.back();</script>"
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if request.method == 'POST':
        cursor.execute("UPDATE notice SET title=%s, content=%s WHERE id=%s", (request.form.get('title'), request.form.get('content'), notice_id))
        conn.commit()
        conn.close()
        return redirect(url_for('notice_detail', notice_id=notice_id))
    cursor.execute("SELECT * FROM notice WHERE id = %s", (notice_id,))
    notice = cursor.fetchone()
    conn.close()
    return render_template('notice_write.html', notice=notice, is_edit=True)

@app.route('/notice/delete/<int:notice_id>')
def notice_delete(notice_id):
    if session.get('user', {}).get('role') != 'admin': return "<script>alert('관리자 권한이 없습니다.'); history.back();</script>"
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notice WHERE id = %s", (notice_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('notice_list'))

# --- AI 진단 및 유저 라우트 ---
@app.route('/diagnose', methods=['POST'])
def diagnose():
    if 'user' not in session: return jsonify({"status": "invalid", "message": "로그인이 필요합니다."}), 401
    
    # 데이터 수집
    user_input = request.form.get('problem', '').strip()
    image_files = request.files.getlist('image')
    has_images = any(f.filename != '' for f in image_files)
    
    # 글도 없고 사진도 없으면 차단
    if not user_input and not has_images: 
        return jsonify({"status": "invalid", "message": "증상이나 사진을 첨부해주세요."}), 400

    try:
        # [RAG 로직] 글(user_input)이 있을 때만 실행됨
        context, sources = "관련 매뉴얼 내용을 찾을 수 없습니다.", ["일반 지식"]
        if user_input:
            emb_res = client.embeddings.create(input=user_input.replace("\n", " "), model="text-embedding-3-small")
            rpc_res = supabase.rpc("match_documents", {"query_embedding": emb_res.data[0].embedding, "match_threshold": 0.25, "match_count": 6}).execute()
            if rpc_res.data:
                documents = rpc_res.data
                sources = list(set([doc.get('metadata', {}).get('source', '일반 지식') for doc in documents]))
                context = "\n\n".join([f"[{doc['metadata'].get('source')}] {doc['content']}" for doc in documents])

        ai_query = user_input if user_input else "사진 속의 집수리 문제를 분석하고 해결책을 제시해줘."
        
        # [프롬프트]
        text_content = f"""당신은 20년 경력의 베테랑 집수리 및 설비 전문 AI '홈픽스'입니다.
사용자의 질문이나 첨부된 사진을 분석하여 원인과 해결책을 정확한 JSON 형태로 진단해 주세요.

[🛠️ 진단 영역 및 예외 처리 가이드]
1. 허용 (집수리 및 설비 영역)
   - 배관/설비(변기/싱크대 막힘, 누수 등), 인테리어, 가구 조립, 공구 사용법, 간단한 조명 교체 등 주거 공간의 유지보수.
   - 🚨 핵심 규칙: "화장실 변기가 막혔어요", "물이 안 내려가요" 같이 짧은 증상이라도 완벽한 '집수리 영역'입니다. 가장 흔한 원인과 해결책(예: 뚫어뻥 사용법)을 추론하여 진단하세요.

2. 고위험 작업 차단 (안전 최우선)
   - 가스 배관 작업, 메인 배전반(두꺼비집) 내부 조작, 건물 구조 변경 등 화재나 생명에 치명적인 위험이 있는 작업.
   - 이 경우 해결책(steps)에 셀프 수리 방법을 절대 적지 말고, "안전상의 이유로 반드시 전문 업체를 부르셔야 합니다."라고 안내하세요. (risk_level: 5 부여)

3. 차단 (비관련 및 가전제품 영역)
   - 프로그래밍, IT 기술, 단순 인사말, 정치, 경제, 요리 등 무관한 주제.
   - TV, 세탁기, 스마트폰 등 '가전제품 내부 회로 및 부품 수리' (이는 제조사 AS 영역입니다).
   - 🚨 차단 대상일 경우 무조건 아래 JSON 형태로만 응답하세요:
   {{"problem_name": "진단 불가", "risk_level": 1, "estimated_cost": "-", "warning": "저는 주거 공간의 유지보수 전용 AI입니다. 무관한 질문이거나 사진 판독이 어렵습니다.", "steps": ["입력하신 내용은 집수리 진단 범위를 벗어났거나 사진이 불명확합니다.", "수리가 필요한 곳의 사진을 선명하게 다시 첨부해 주세요."], "tools": [], "sources": ""}}

[JSON 출력 규격 및 데이터 타입]
- risk_level: 1(매우 쉬움/안전) ~ 5(전문가 필요/위험) 사이의 정수(Integer).
- tools: 필요한 공구가 없다면 빈 배열 [] 출력.

[학습 지식]
{context}

[사용자 입력]
{ai_query}

위 가이드를 준수하여 [정상 답변 형식 JSON]에 맞춰 구체적이고 전문적인 답변을 생성하세요.
"""

        messages_content = [{"type": "text", "text": text_content}]
        
        # ✨ [핵심 1] 화면에 뿌려줄 사진 리스트 초기화
        encoded_images = [] 
        
        # ✨ [핵심 2] 글의 유무와 상관없이, 사진이 있으면 무조건 인코딩해서 리스트에 추가
        if has_images:
            for image_file in image_files:
                if image_file and image_file.filename != '':
                    base64_image = encode_image(image_file)
                    if base64_image:
                        messages_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}})
                        encoded_images.append(base64_image)

        # AI 응답 받기
        response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": messages_content}], response_format={"type": "json_object"})
        result = json.loads(response.choices[0].message.content)
        
        result['risk_level'] = result.get('risk_level', 1)
        result['warning'] = result.get('warning', '주의사항 없음')
        
        # ✨ [핵심 3] 결과 객체에 내가 올린 사진 리스트 합치기
        result['images'] = encoded_images

        # DB 저장
        if result.get('problem_name') != '진단 불가':
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO history (userid, problem_name, steps, tools, estimated_cost, risk_level, warning) VALUES (%s, %s, %s, %s, %s, %s, %s)',
                (session['user']['userid'], result.get('problem_name', '사진 진단'), json.dumps(result.get('steps', []), ensure_ascii=False), json.dumps(result.get('tools', []), ensure_ascii=False), result.get('estimated_cost', '비용 정보 없음'), result['risk_level'], result['warning'])
            )
            conn.commit()
            cursor.close(); conn.close()
            
        session['last_result'] = result
        return jsonify(result)
        
    except Exception as e:
        print(f"❌ 진단 프로세스 오류: {e}")
        return jsonify({"status": "error", "message": "오류가 발생했습니다."}), 500

@app.route('/history/<int:history_id>')
def history_detail(history_id):
    if 'user' not in session: return redirect('/login')
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM history WHERE id = %s AND userid = %s', (history_id, session['user']['userid']))
    row = cursor.fetchone()
    cursor.close(); conn.close()
    
    if row is None: return "<script>alert('내역을 찾을 수 없습니다.'); history.back();</script>"
    result_data = {
        "problem_name": row['problem_name'], "steps": json.loads(row['steps']), "tools": json.loads(row['tools']),
        "estimated_cost": row['estimated_cost'] if row['estimated_cost'] else "비용 정보 없음",
        "risk_level": row['risk_level'], "warning": row['warning']
    }
    return render_template('result.html', result_data=result_data)

@app.route('/check_id', methods=['POST'])
def check_id():
    uid = request.get_json().get('userid')
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
        uid = request.form.get('userid')
        pw = request.form.get('password')
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM users WHERE userid = %s', (uid,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if user and check_password_hash(user['password'], pw):
            if user.get('status') == 'Deleted':
                return "<script>alert('탈퇴 처리된 계정입니다.'); history.back();</script>"
            
            if user.get('approval_status') == 'pending':
                return "<script>alert('가입 서류 심사 중입니다. 관리자 승인 후 서비스 이용이 가능합니다.'); history.back();</script>"
            
            # 세션 정보 저장
            session['user'] = user 
            session['userid'] = user['userid']
            session['name'] = user.get('name', '고객')
            
            user_role = str(user.get('role', '')).strip().lower()
            session['role'] = user_role
            
            # ✨ [수정 완료] 
            # 전문가, 임대인, 일반 사용자 모두 로그인 성공 시 메인 화면(index)으로 이동합니다.
            # 대시보드는 상단 바의 버튼을 통해서만 진입하게 됩니다.
            return redirect(url_for('index'))
                
        return "<script>alert('틀린 정보입니다.'); history.back();</script>"
        
    return render_template('login.html')

# 파일이 저장될 폴더 경로 설정
UPLOAD_FOLDER = 'static/uploads/documents'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ==========================================
# 관리자: 가입 승인 처리 API
# ==========================================
@app.route('/admin/approve_user/<userid>', methods=['POST'])
def approve_user(userid):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET approval_status = 'approved' WHERE userid = %s", (userid,))
        conn.commit()
        return jsonify({'result': 'success', 'message': '승인이 완료되었습니다.'})
    except Exception as e:
        return jsonify({'result': 'fail', 'message': str(e)})
    finally:
        cursor.close()
        conn.close()

# ==========================================
# 관리자: 가입 거절(삭제) 처리 API
# ==========================================
@app.route('/admin/reject_user/<userid>', methods=['POST'])
def reject_user(userid):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE userid = %s", (userid,))
        conn.commit()
        return jsonify({'result': 'success', 'message': '가입이 거절 및 삭제되었습니다.'})
    except Exception as e:
        return jsonify({'result': 'fail', 'message': str(e)})
    finally:
        cursor.close()
        conn.close()

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        d = request.form
        f = request.files
        
        hashed_pw = generate_password_hash(d['password'])
        role = d.get('role', 'user') 
        approval_status = 'pending' if role in ['expert', 'landlord'] else 'approved'
        
        def save_file(file_obj, userid):
            if file_obj and file_obj.filename:
                ext = file_obj.filename.rsplit('.', 1)[-1].lower()
                filename = f"{userid}_{uuid.uuid4().hex[:8]}.{ext}"
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                file_obj.save(filepath)
                return f"/{filepath}" 
            return None

        idcard_path = save_file(f.get('idcard_file'), d['userid'])
        bizreg_path = save_file(f.get('bizreg_file'), d['userid'])
        estate_path = save_file(f.get('estate_file'), d['userid'])

        birthdate = d.get('birthdate')
        if not birthdate:
            birthdate = None

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO users 
                (name, userid, password, email, birthdate, phone, role, idcard_file, bizreg_file, estate_file, approval_status) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                d['name'], d['userid'], hashed_pw, d['email'], birthdate, d['phone'], 
                role, idcard_path, bizreg_path, estate_path, approval_status
            ))
            conn.commit()
            
            if approval_status == 'pending':
                msg = '가입 서류가 접수되었습니다. 관리자 승인 후 로그인 가능합니다.'
            else:
                msg = '가입 성공!'
                
            return f"<script>alert('{msg}'); location.href='/login';</script>"
        except Exception as e: 
            return f"<script>alert(`오류 발생: {e}`); history.back();</script>"
        finally: 
            cursor.close(); conn.close()
            
    return render_template('signup.html')

@app.route('/find-id', methods=['GET', 'POST'])
def find_id():
    if request.method == 'POST':
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT userid FROM users WHERE name = %s AND email = %s', (request.form.get('name'), request.form.get('email')))
        user = cursor.fetchone()
        cursor.close(); conn.close()
        if user: return f"<script>alert('회원님의 아이디는 [{user['userid']}] 입니다.'); location.href='/login';</script>"
        else: return "<script>alert('일치하는 정보가 없습니다.'); history.back();</script>"
    return render_template('find_id.html')

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM users WHERE userid = %s AND email = %s', (request.form.get('userid'), request.form.get('email')))
        user = cursor.fetchone()
        
        if user:
            cursor.execute('UPDATE users SET password = %s WHERE id = %s', (generate_password_hash(request.form.get('new_password')), user['id']))
            conn.commit(); cursor.close(); conn.close()
            return "<script>alert('비밀번호가 성공적으로 변경되었습니다.'); location.href='/login';</script>"
        else:
            cursor.close(); conn.close()
            return "<script>alert('아이디 또는 이메일 정보가 일치하지 않습니다.'); history.back();</script>"
            
    return render_template('reset_password.html')

@app.route('/change_password', methods=['POST'])
def change_password():
    if 'user' not in session: return redirect('/login')
    if request.form.get('new_password') != request.form.get('confirm_password'): return "<script>alert('비밀번호 확인 불일치.'); history.back();</script>"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM users WHERE userid = %s', (session['user']['userid'],))
    user = cursor.fetchone()

    if user and check_password_hash(user['password'], request.form.get('current_password')):
        cursor.execute('UPDATE users SET password = %s WHERE userid = %s', (generate_password_hash(request.form.get('new_password')), session['user']['userid']))
        conn.commit(); cursor.close(); conn.close()
        session.clear()
        return "<script>alert('비밀번호가 변경되었습니다. 다시 로그인해주세요.'); location.href='/login';</script>"
    else:
        cursor.close(); conn.close()
        return "<script>alert('현재 비밀번호가 틀렸습니다.'); history.back();</script>"
        
# 1. 전문가 대시보드 홈 (stats와 tasks 데이터를 가져옵니다)
@app.route('/expert_dashboard')
def expert_dashboard():
    if 'userid' not in session or session.get('role') != 'expert':
        return "<script>alert('업체 회원 전용 페이지입니다.'); location.href='/login';</script>"
    
    expert_id = session['userid']
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # 통계 데이터 가져오기
        cursor.execute("""
    SELECT 
        -- ✨ 수리 완료/결제 완료가 아닌 것만 '진행 중'으로 카운트
        COUNT(CASE WHEN status NOT IN ('수리완료', '결제완료') THEN 1 END) as pending_count,
        -- ✨ 결제가 완료된 금액만 총 수입으로 합산
        COALESCE(SUM(CASE WHEN status = '결제완료' THEN actual_cost ELSE 0 END), 0) as total_income
    FROM repair_logs 
    WHERE expert_id = %s
""", (expert_id,))
        stats = cursor.fetchone()

        # ✨ 수정된 쿼리: r.building_id 대신 u.building_id를 사용해 JOIN 합니다.
        cursor.execute("""
            SELECT r.id, b.building_name, r.room_number, r.problem_name, r.status, 
                   DATE_FORMAT(r.created_at, '%Y-%m-%d') as created_at
            FROM repair_logs r
            JOIN users u ON r.tenant_id = u.userid
            JOIN buildings b ON u.building_id = b.id
            WHERE r.expert_id = %s 
            ORDER BY r.created_at DESC LIMIT 5
        """, (expert_id,))
        tasks = cursor.fetchall()

    except Exception as e:
        print(f"❌ 전문가 대시보드 에러: {e}")
        stats = {"pending_count": 0, "total_income": 0}
        tasks = []
    finally:
        cursor.close(); conn.close()

    return render_template('expert_dashboard.html', user_info=session.get('user'), stats=stats, tasks=tasks)

# 2. 수리 대기 목록(expert_tasks) 수정
@app.route('/expert/tasks')
def expert_tasks():
    if 'userid' not in session or session.get('role') != 'expert':
        return redirect('/login')

    expert_id = session['userid']
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # ✨ 수정된 쿼리: JOIN 구조를 변경하여 에러를 방지합니다.
        cursor.execute("""
            SELECT r.id, b.building_name, r.room_number, r.problem_name, r.status, 
                   DATE_FORMAT(r.created_at, '%Y-%m-%d %H:%i') as created_at
            FROM repair_logs r
            JOIN users u ON r.tenant_id = u.userid
            JOIN buildings b ON u.building_id = b.id
            WHERE r.expert_id = %s 
            ORDER BY r.created_at DESC
        """, (expert_id,))
        tasks = cursor.fetchall()
    except Exception as e:
        print(f"❌ 수리 목록 로드 에러: {e}")
        tasks = []
    finally:
        cursor.close(); conn.close()

    return render_template('expert_tasks.html', tasks=tasks, user_info=session.get('user'))

@app.route('/expert/submit_receipt', methods=['POST'])
def submit_receipt():
    if 'userid' not in session: 
        return jsonify({"result": "fail"}), 401
    
    order_id = request.form.get('order_id')
    final_cost = request.form.get('final_cost')
    image_file = request.files.get('receipt_image')
    
    filename = ""
    if image_file and image_file.filename != '':
        filename = secure_filename(f"receipt_{order_id}_{image_file.filename}")
        
        # ✨ [핵심 해결책] 사진을 저장할 폴더 경로를 지정하고, 폴더가 없으면 자동으로 만듭니다!
        upload_folder = os.path.join('static', 'uploads', 'reviews')
        os.makedirs(upload_folder, exist_ok=True) 
        
        # 이제 폴더가 무조건 존재하므로 안전하게 저장됩니다.
        image_file.save(os.path.join(upload_folder, filename))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE repair_logs 
            SET status = '영수증제출', actual_cost = %s, receipt_image = %s 
            WHERE id = %s
        """, (final_cost, filename, order_id))
        conn.commit()
    except Exception as e:
        print(f"❌ 영수증 제출 에러: {e}")
        return "<script>alert('처리 중 오류가 발생했습니다.'); history.back();</script>"
    finally:
        cursor.close()
        conn.close()

    return f"<script>alert('영수증이 성공적으로 제출되었습니다.'); location.href='/expert/tasks';</script>"

@app.route('/landlord_dashboard')
def landlord_dashboard():
    if 'userid' not in session or session.get('role') != 'landlord':
        session.clear() 
        return "<script>alert('임대인 전용 페이지입니다. 다시 로그인해주세요.'); location.href='/login';</script>"
    
    landlord_id = session['userid']
    landlord_name = session.get('name')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 기본값 세팅
    tenant_count, pending_requests, total_spend = 0, 0, 0
    logs, experts_list = [], []
    building = {
        "building_name": "전체 건물 통합 관리", 
        "address": "운영 중인 모든 건물의 실시간 현황입니다."
    }

    try:
        # 💡 [안전장치 1] 전문가 목록부터 무조건 먼저 가져옵니다!
        cursor.execute("SELECT userid, name FROM users WHERE role = 'expert'")
        experts_list = cursor.fetchall()
        print(f"✅ 불러온 전문가 수: {len(experts_list)}명") # 터미널에서 몇 명인지 확인해보세요

        # 💡 [안전장치 2] 나머지 통계 데이터 가져오기
        cursor.execute("""
            SELECT COUNT(*) as count 
            FROM users u
            JOIN buildings b ON u.building_id = b.id
            WHERE b.landlord_id = %s AND u.role = 'user'
        """, (landlord_id,))
        tenant_count = cursor.fetchone()['count']

        cursor.execute("""
            SELECT 
                COUNT(CASE WHEN status = '요청됨' THEN 1 END) as pending_requests,
                COALESCE(SUM(actual_cost), 0) as total_cost 
            FROM repair_logs 
            WHERE landlord_id = %s
        """, (landlord_id,))
        stats_raw = cursor.fetchone()
        
        pending_requests = stats_raw['pending_requests'] if stats_raw else 0
        total_spend = stats_raw['total_cost'] if stats_raw else 0

        # 💡 SELECT 부분에 r.receipt_image 를 추가했습니다!
        cursor.execute("""
            SELECT 
                r.id, DATE_FORMAT(r.created_at, '%Y-%m-%d') as created_at, 
                r.room_number, r.problem_name, r.status, r.receipt_image,
                b.building_name, u.name as tenant_name
            FROM repair_logs r
            LEFT JOIN users u ON r.tenant_id = u.userid
            LEFT JOIN buildings b ON u.building_id = b.id  /* ✨ r.building_id ➔ u.building_id 로 수정됨! */
            WHERE r.landlord_id = %s 
            ORDER BY r.created_at DESC LIMIT 10
        """, (landlord_id,))
        logs = cursor.fetchall()

    except Exception as e:
        print(f"❌ 임대인 대시보드 DB 에러: {e}")
        building["building_name"] = "데이터 로드 실패"
        building["address"] = "DB 구조와 쿼리가 일치하는지 확인이 필요합니다."
        
    finally:
        cursor.close()
        conn.close()

    # HTML 템플릿으로 전송
    return render_template('landlord_dashboard.html', 
                           user_info=session,
                           landlord_name=landlord_name,
                           building=building,
                           tenant_count=tenant_count,    
                           pending_requests=pending_requests, 
                           total_spend=total_spend,      
                           logs=logs,
                           experts=experts_list) # 모달창을 위해 전송!

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

@app.route('/result')
def result_page():
    res = session.get('last_result')
    if not res: return redirect('/')
    
    # ✨ 세션에 저장된 'images'를 안전하게 꺼내서 템플릿으로 전달합니다.
    safe_data = {
        'problem_name': res.get('problem_name', '진단 결과'),
        'images': res.get('images', []),  # 👈 핵심 포인트
        'steps': res.get('steps', []),
        'tools': res.get('tools', []),
        'estimated_cost': res.get('estimated_cost', '비용 정보 없음'),
        'risk_level': res.get('risk_level', 1),
        'warning': res.get('warning', '주의사항 없음')
    }
    return render_template('result.html', result_data=safe_data)

@app.route('/myinfo')
def myinfo():
    if 'user' not in session: 
        return redirect('/login')
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM users WHERE userid = %s", (session['user']['userid'],))
    fresh_user_info = cursor.fetchone()
    
    session['user'] = fresh_user_info
    session.modified = True
    
    cursor.execute('SELECT * FROM history WHERE userid = %s ORDER BY created_at DESC', (session['user']['userid'],))
    history = cursor.fetchall()
    
    cursor.execute('SELECT * FROM reviews WHERE userid = %s', (session['user']['userid'],))
    my_reviews = cursor.fetchall()
    reviews_dict = {r['repair_item']: r for r in my_reviews}

    # 내 포인트 내역 최신순으로 가져오기
    cursor.execute('SELECT * FROM point_history WHERE userid = %s ORDER BY created_at DESC', (session['user']['userid'],))
    point_logs = cursor.fetchall()
    
    cursor.execute("""
        SELECT b.id, b.landlord_id, b.building_name, b.address, u.name as landlord_name 
        FROM buildings b
        JOIN users u ON b.landlord_id = u.userid
    """)
    all_buildings = cursor.fetchall()
    
    cursor.close(); conn.close()
    
    return render_template('myinfo.html', 
                           user_info=fresh_user_info, 
                           history=history, 
                           reviews_dict=reviews_dict, 
                           point_logs=point_logs,
                           all_buildings=all_buildings) # ✨ 템플릿으로 전달!

# ✨ [신규] 거주지 정보 및 개인정보 동의 업데이트 라우트
@app.route('/update_residence', methods=['POST'])
def update_residence():
    if 'user' not in session: return redirect('/login')
    
    userid = session['user']['userid']
    # ✨ landlord_id 대신 building_id를 받습니다.
    building_id = request.form.get('building_id') 
    room_number = request.form.get('room_number')
    privacy_consent = request.form.get('privacy_consent')
    
    if not building_id or not room_number or not privacy_consent:
        return "<script>alert('건물 선택, 호수 입력 및 개인정보 동의가 필요합니다.'); history.back();</script>"
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # ✨ users 테이블의 building_id를 업데이트
        cursor.execute("""
            UPDATE users SET building_id = %s, room_number = %s 
            WHERE userid = %s
        """, (building_id, room_number, userid))
        
        # (선택) 만약 users 테이블에 landlord_id도 따로 저장해야 한다면
        # UPDATE users SET building_id = %s, landlord_id = (SELECT landlord_id FROM buildings WHERE id = %s), ... 형태로 작성할 수도 있습니다.
        
        conn.commit()
        return "<script>alert('거주지 정보가 성공적으로 저장되었습니다.'); location.href='/myinfo';</script>"
    except Exception as e:
        print(f"거주지 저장 오류: {e}")
        return "<script>alert('저장 중 오류가 발생했습니다.'); history.back();</script>"
    finally:
        cursor.close()
        conn.close()

# ✨ [수정됨] 임대인 전용 건물 등록 라우트 (개인정보 동의 확인 포함)
@app.route('/register_building', methods=['POST'])
def register_building():
    if 'user' not in session or session['user']['role'] != 'landlord':
        return "<script>alert('임대인 권한이 필요합니다.'); history.back();</script>"
        
    landlord_id = session['user']['userid']
    building_name = request.form.get('building_name')
    address = request.form.get('address')
    privacy_consent = request.form.get('privacy_consent') # ✨ 폼에서 동의 여부 가져오기
    
    # ✨ 유효성 검사 (하나라도 비어있거나 동의 안 하면 차단)
    if not building_name or not address or not privacy_consent:
        return "<script>alert('건물 정보 입력 및 정보 공개 동의가 필요합니다.'); history.back();</script>"
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # buildings 테이블에 새 건물 저장
        cursor.execute("""
            INSERT INTO buildings (landlord_id, building_name, address)
            VALUES (%s, %s, %s)
        """, (landlord_id, building_name, address))
        conn.commit()
        return "<script>alert('새 건물이 성공적으로 등록되었습니다! 이제 세입자가 선택할 수 있습니다.'); location.href='/myinfo';</script>"
    except Exception as e:
        print(f"건물 등록 오류: {e}")
        return "<script>alert('건물 등록 중 오류가 발생했습니다.'); history.back();</script>"
    finally:
        cursor.close()
        conn.close()

@app.route('/edit_review', methods=['POST'])
def edit_review():
    if 'user' not in session: return "<script>alert('로그인이 필요합니다.'); history.back();</script>"
    
    item = request.form.get('repair_item')
    contractor = request.form.get('contractor_name')
    cost = request.form.get('cost')
    rating = request.form.get('rating')
    comment = request.form.get('comment')
    image_file = request.files.get('image')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if image_file and image_file.filename != '':
        filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{image_file.filename}")
        upload_path = os.path.join('static', 'uploads', 'reviews')
        if not os.path.exists(upload_path): os.makedirs(upload_path)
        image_file.save(os.path.join(upload_path, filename))
        
        cursor.execute('''UPDATE reviews 
                          SET contractor_name=%s, cost=%s, rating=%s, comment=%s, image_path=%s 
                          WHERE userid=%s AND repair_item=%s''',
                       (contractor, cost, rating, comment, filename, session['user']['userid'], item))
    else:
        cursor.execute('''UPDATE reviews 
                          SET contractor_name=%s, cost=%s, rating=%s, comment=%s 
                          WHERE userid=%s AND repair_item=%s''',
                       (contractor, cost, rating, comment, session['user']['userid'], item))
                       
    conn.commit()
    cursor.close(); conn.close()
    
    return "<script>alert('후기가 성공적으로 수정되었습니다!'); location.href='/review';</script>"

@app.route('/delete_history/<int:history_id>', methods=['POST'])
def delete_history(history_id):
    if 'user' not in session: return jsonify({'status':'error'}), 401
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM history WHERE id = %s AND userid = %s', (history_id, session['user']['userid']))
    conn.commit(); cursor.close(); conn.close()
    return jsonify({"status": "success"})

@app.route('/support', methods=['GET', 'POST'])
def support():
    if request.method == 'POST':
        if 'user' not in session: return redirect('/login')
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO support (userid, title, content) VALUES (%s, %s, %s)', 
                       (session['user']['userid'], request.form.get('title'), request.form.get('content')))
        conn.commit(); cursor.close(); conn.close()
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
    user_session = session.get('user')
    if not user_session: 
        return jsonify({"result": "fail", "message": "로그인이 필요합니다."}), 401
        
    user_uid = user_session.get('userid')
    user_id_pk = user_session.get('id')
    
    UPLOAD_DIR = "static/uploads/documents"

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT idcard_file, bizreg_file, estate_file FROM users WHERE id = %s", (user_id_pk,))
        user_files = cursor.fetchone()

        if user_files:
            for key in ['idcard_file', 'bizreg_file', 'estate_file']:
                file_name = user_files.get(key)
                if file_name:
                    clean_name = file_name.lstrip('/') 
                    if not clean_name.startswith('static/'):
                        file_path = os.path.join(UPLOAD_DIR, clean_name)
                    else:
                        file_path = clean_name
                    
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            print(f"✅ 삭제 완료: {file_path}")
                        except Exception as e:
                            print(f"❌ 삭제 에러: {e}")

        cursor.execute('DELETE FROM history WHERE userid = %s', (user_uid,))
        cursor.execute('DELETE FROM support WHERE userid = %s', (user_uid,))
        cursor.execute('DELETE FROM reviews WHERE userid = %s', (user_uid,))
        cursor.execute('DELETE FROM users WHERE id = %s', (user_id_pk,))
        
        conn.commit()
        session.clear()
        return jsonify({"result": "success", "message": "성공적으로 삭제되었습니다."}), 200
    except Exception as e:
        return jsonify({"result": "fail", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/terms')
def terms(): return render_template('terms.html')

@app.route('/privacy')
def privacy(): return render_template('privacy.html')

# ==========================================
# [2] 👑 관리자(Admin) 전용 라우트
# ==========================================

@app.route('/admin_dashboard')
def admin_dashboard_view():
    if session.get('user', {}).get('role') != 'admin': 
        return "<script>alert('관리자 전용 페이지입니다.'); location.href='/';</script>"
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor() 
        
        # 1. 기본 카운트 통계
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM history")
        total_diagnoses = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM reservations WHERE status = '예약대기'")
        total_reservations = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM support WHERE status != '답변완료'")
        pending_support = cursor.fetchone()[0]
        
        # 2. 유저 역할별 분포
        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        admin_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'expert'")
        expert_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'landlord'")
        landlord_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE role NOT IN ('admin', 'expert', 'landlord')")
        general_users = cursor.fetchone()[0]

        # 3. 항목별 진단 분포 (TOP 5)
        cursor.execute("SELECT problem_name, COUNT(*) as cnt FROM history GROUP BY problem_name ORDER BY cnt DESC LIMIT 5")
        stats = cursor.fetchall()
        labels = [s[0] for s in stats] if stats else ["데이터없음"]
        counts = [s[1] for s in stats] if stats else [0]

        # 4. ✨ [신규] 최근 7일간 일별 진단 건수 추이
        cursor.execute("""
            SELECT DATE_FORMAT(created_at, '%m-%d') as date, COUNT(*) 
            FROM history 
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            GROUP BY date 
            ORDER BY date ASC
        """)
        trend_data = cursor.fetchall()
        trend_labels = [t[0] for t in trend_data]
        trend_counts = [t[1] for t in trend_data]
        
        cursor.close(); conn.close()
        
        return render_template('admin_dashboard.html', 
                               total_users=total_users, 
                               total_diagnoses=total_diagnoses,
                               total_reservations=total_reservations,
                               pending_support=pending_support,
                               admin_users=admin_users,
                               expert_users=expert_users,
                               landlord_users=landlord_users,
                               general_users=general_users,
                               labels=labels, 
                               counts=counts,
                               trend_labels=trend_labels,
                               trend_counts=trend_counts)
    except Exception as e:
        return f"대시보드 로드 실패: {e}"
    
@app.route('/admin/users')
def admin_user_management():
    if session.get('user', {}).get('role') != 'admin': 
        return redirect('/')
    
    search_keyword = request.args.get('search', '').strip()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        order_logic = """
            ORDER BY 
                CASE WHEN approval_status = 'pending' THEN 0 ELSE 1 END,
                FIELD(role, 'admin', 'expert', 'landlord', 'user'),
                name ASC
        """
        
        if search_keyword:
            query = f"""
                SELECT *, 
                DATE_ADD(last_seen, INTERVAL 9 HOUR) AS last_seen_kst,
                CASE 
                    WHEN last_seen >= NOW() - INTERVAL 5 MINUTE THEN '온라인'
                    ELSE '오프라인'
                END AS is_online 
                FROM users 
                WHERE name LIKE %s
                {order_logic}
            """
            cursor.execute(query, (f"%{search_keyword}%",))
        else:
            query = f"""
                SELECT *, 
                DATE_ADD(last_seen, INTERVAL 9 HOUR) AS last_seen_kst,
                CASE 
                    WHEN last_seen >= NOW() - INTERVAL 5 MINUTE THEN '온라인'
                    ELSE '오프라인'
                END AS is_online 
                FROM users 
                {order_logic}
            """
            cursor.execute(query)
            
        users_list = cursor.fetchall()
        cursor.close(); conn.close()
        
        return render_template('admin_users.html', users=users_list, search_keyword=search_keyword)
    except Exception as e:
        return f"회원관리 로드 실패: {e}"

# ✨ [업데이트 됨] 관리자 포인트 변경 및 내역 자동 기록
@app.route('/admin/update_points', methods=['POST'])
def admin_update_points():
    if session.get('user', {}).get('role') != 'admin': 
        return "<script>alert('권한이 없습니다.'); history.back();</script>"
    
    try:
        target_userid = request.form.get('userid')
        new_points = int(request.form.get('points', 0))
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # 1. 기존 포인트를 확인하여 차액 계산
        cursor.execute("SELECT points FROM users WHERE userid = %s", (target_userid,))
        user_data = cursor.fetchone()
        
        if user_data:
            current_points = user_data.get('points') or 0
            point_diff = new_points - current_points
            
            # 2. 포인트 업데이트
            cursor.execute("UPDATE users SET points = %s WHERE userid = %s", (new_points, target_userid))
            
            # 3. 변동이 있을 때만 내역(History) 기록
            if point_diff != 0:
                description = "관리자 직권 조정"
                cursor.execute(
                    "INSERT INTO point_history (userid, amount, description) VALUES (%s, %s, %s)",
                    (target_userid, point_diff, description)
                )
                
        conn.commit()
        cursor.close(); conn.close()
        
        return "<script>alert('회원 포인트가 변경되었으며 내역에 기록되었습니다!'); history.back();</script>"
    except Exception as e: 
        return f"<script>alert('포인트 변경 실패: {e}'); history.back();</script>"

@app.route('/admin/restore_user/<string:user_id>', methods=['POST'])
def admin_restore_user(user_id):
    if session.get('user', {}).get('role') != 'admin': return jsonify({"result": "fail", "message": "권한이 없습니다."})
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET status = 'Active', deleted_at = NULL WHERE userid = %s", (user_id,))
        conn.commit()
        affected_rows = cursor.rowcount
        cursor.close(); conn.close()
        if affected_rows > 0: return jsonify({"result": "success", "message": f"{user_id} 계정이 다시 활성화되었습니다."})
        return jsonify({"result": "fail", "message": "사용자를 찾을 수 없습니다."})
    except Exception as e: return jsonify({"result": "fail", "message": str(e)})

@app.route('/admin/delete_user/<string:user_id>', methods=['POST'])
def admin_delete_user(user_id):
    if session.get('user', {}).get('role') != 'admin': return jsonify({"result": "fail", "message": "권한이 없습니다."})
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("UPDATE users SET status = 'Deleted', deleted_at = %s WHERE userid = %s", (now, user_id))
        conn.commit()
        cursor.close(); conn.close()
        return jsonify({"result": "success", "message": "즉시 탈퇴 처리되었습니다. (데이터는 30일 후 자동 파기)"})
    except Exception as e: return jsonify({"result": "fail", "message": str(e)})

@app.route('/admin/history')
def admin_diagnosis_history():
    if session.get('user', {}).get('role') != 'admin': return redirect('/')
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM history ORDER BY created_at DESC")
        logs_data = cursor.fetchall()
        cursor.close(); conn.close()
        return render_template('admin_history.html', logs_list=logs_data)
    except Exception as e:
        return f"진단 로그 로드 오류: {e}"

@app.route('/admin/experts')
def admin_expert_management():
    if session.get('user', {}).get('role') != 'admin': return redirect('/')
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, business_no, specialty, status FROM experts ORDER BY id DESC")
        expert_list = cursor.fetchall()
        cursor.close(); conn.close()
        return render_template('admin_experts.html', experts=expert_list)
    except Exception as e:
        return f"업체관리 로드 실패: {e}"

@app.route('/admin/support')
def admin_support_manage():
    if session.get('user', {}).get('role') != 'admin': return "<script>alert('관리자만 접근 가능합니다.'); history.back();</script>"
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM support ORDER BY created_at DESC")
        all_supports = cursor.fetchall()
    finally:
        cursor.close(); conn.close()
    return render_template('admin_support.html', supports=all_supports)

@app.route('/admin/support/answer/<int:post_id>', methods=['POST'])
def admin_support_answer(post_id):
    if session.get('user', {}).get('role') != 'admin': return "<script>alert('권한이 없습니다.'); history.back();</script>"
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE support SET answer = %s, status = '답변완료' WHERE id = %s", (request.form.get('answer'), post_id))
        conn.commit()
    finally:
        cursor.close(); conn.close()
    return "<script>alert('답변이 등록되었습니다.'); location.href='/admin/support';</script>"

@app.route('/admin/reservations')
def admin_reservations():
    if session.get('user', {}).get('role') != 'admin': return redirect('/')
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM reservations ORDER BY created_at DESC")
        res_list = cursor.fetchall()
        cursor.close(); conn.close()
        return render_template('admin_reservations.html', reservations=res_list)
    except Exception as e:
        return f"예약 관리 로드 실패: {e}"
    

# ✨ [업데이트 됨] 예약 취소 시 환불 내역 기록 및 맞춤형 알림 메시지 추가
@app.route('/admin/update_reservation', methods=['POST'])
def admin_update_reservation():
    if session.get('user', {}).get('role') != 'admin': 
        return jsonify({"result": "fail", "message": "권한이 없습니다."})
    
    data = request.get_json()
    res_id = data.get('id')
    new_status = data.get('status')
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT userid, used_points, status FROM reservations WHERE id = %s", (res_id,))
        res_info = cursor.fetchone()
        
        if not res_info:
            return jsonify({"result": "fail", "message": "예약 정보를 찾을 수 없습니다."})
            
        cursor.execute("UPDATE reservations SET status = %s WHERE id = %s", (new_status, res_id))
        
        # 💡 기본 알림 메시지 설정 (예약대기, 예약완료 등의 경우 이 메시지만 출력됨)
        success_msg = f"상태가 '{new_status}'(으)로 변경되었습니다."
        
        # 예약이 취소되었을 때 포인트 환불 및 내역 기록
        if new_status == '예약취소' and res_info['status'] != '예약취소':
            cursor.execute("UPDATE users SET points = IFNULL(points, 0) + %s WHERE userid = %s", 
                           (res_info['used_points'], res_info['userid']))
            
            # 포인트 환불 내역 기록 (실제로 사용한 포인트가 있을 때만)
            if res_info['used_points'] > 0:
                cursor.execute("INSERT INTO point_history (userid, amount, description) VALUES (%s, %s, %s)",
                               (res_info['userid'], res_info['used_points'], "예약 취소로 인한 포인트 환불"))
                
                # 💡 취소이면서 환불된 포인트가 있을 때만 알림 메시지에 내용 추가
                success_msg += f" (사용된 {res_info['used_points']}P 환불 완료)"
            
            if session['user']['userid'] == res_info['userid']:
                session['user']['points'] = session['user'].get('points', 0) + res_info['used_points']
                session.modified = True
                           
        conn.commit()
        cursor.close(); conn.close()
        
        # 유동적으로 변한 success_msg를 HTML로 전달
        return jsonify({"result": "success", "message": success_msg})
    except Exception as e:
        return jsonify({"result": "fail", "message": str(e)})

# [세입자 현황 페이지 로직]
@app.route('/landlord/tenants')
def tenant_management():
    if 'user' not in session: 
        return redirect('/login')
    
    landlord_id = session['user']['userid']
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # ✨ [핵심 해결책] 에러가 나더라도 터지지 않도록 미리 빈 리스트를 만들어 둡니다.
    tenants_list = []
    buildings_list = []
    
    try:
        # 1. 사이드바용 건물 목록 가져오기
        cursor.execute("SELECT id, building_name FROM buildings WHERE landlord_id = %s", (landlord_id,))
        buildings_list = cursor.fetchall()

        # 2. 세입자 목록 가져오기 (연결된 건물의 주인이 나인 사람)
        query = """
            SELECT u.userid, u.name, u.phone, u.email, u.room_number, b.building_name 
            FROM users u
            JOIN buildings b ON u.building_id = b.id
            WHERE b.landlord_id = %s AND u.role = 'user'
        """
        cursor.execute(query, (landlord_id,))
        tenants_list = cursor.fetchall()

    except Exception as e:
        # 에러가 발생하면 터미널에 원인을 붉은 글씨로 출력합니다.
        print(f"🚨 세입자 목록 DB 에러: {e}")
        
    finally:
        cursor.close()
        conn.close()
    
    return render_template('tenant_management.html', 
                           tenants=tenants_list, 
                           buildings_list=buildings_list, 
                           user_info=session['user'])

# ==========================================
# [전체 수리 내역 페이지 로직] 추가됨!
# ==========================================
@app.route('/landlord/repairs')
def landlord_repairs():
    if 'user' not in session: 
        return redirect('/login')
    
    landlord_id = session['user']['userid']
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 1. 사이드바용 건물 리스트 가져오기
        cursor.execute("SELECT id, building_name FROM buildings WHERE landlord_id = %s", (landlord_id,))
        buildings_list = cursor.fetchall()
        
        # 2. 전체 수리 내역 가져오기 (이미 작성하신 쿼리 그대로 사용)
        query = """
            SELECT 
                r.id, 
                DATE_FORMAT(r.created_at, '%Y-%m-%d %H:%i') as created_at, 
                r.room_number, 
                r.problem_name, 
                r.status, 
                r.ai_estimated_cost, 
                r.actual_cost, 
                r.receipt_image,
                u.name as tenant_name
            FROM repair_logs r
            LEFT JOIN users u ON r.tenant_id = u.userid
            WHERE r.landlord_id = %s 
            ORDER BY r.created_at DESC
        """
        cursor.execute(query, (landlord_id,))
        repairs_list = cursor.fetchall()

    except Exception as e:
        print(f"❌ DB 에러 발생: {e}")
        repairs_list = []
        buildings_list = []
    
    finally:
        cursor.close()
        conn.close()
    
    # ✨ [중요] 토스 결제에 필요한 키와 URL을 템플릿으로 보냅니다.
    return render_template('landlord_repairs.html', 
                           repairs=repairs_list, 
                           buildings_list=buildings_list, 
                           user_info=session['user'],
                           toss_client_key=TOSS_CLIENT_KEY,  # ← 추가
                           base_url=BASE_URL)               # ← 추가

# ✨ [신규] 세입자가 AI 진단 후 임대인에게 수리를 요청하는 라우트
@app.route('/user/request_landlord', methods=['POST'])
def request_landlord():
    if 'user' not in session: 
        return "<script>alert('로그인이 필요합니다.'); location.href='/login';</script>"

    user = session['user']
    tenant_id = user['userid']
    building_id = user.get('building_id')
    room_number = user.get('room_number', '미지정')
    
    problem_name = request.form.get('problem_name')
    estimated_cost = request.form.get('estimated_cost')

    # 1. 거주지(건물) 등록 여부 검사
    if not building_id:
        return "<script>alert('먼저 마이페이지에서 현재 거주 중인 건물을 등록해주세요.'); location.href='/myinfo';</script>"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 2. 해당 건물의 임대인(landlord_id) 정보 찾기
        cursor.execute("SELECT landlord_id FROM buildings WHERE id = %s", (building_id,))
        building = cursor.fetchone()
        
        if not building or not building.get('landlord_id'):
            return "<script>alert('해당 건물의 임대인 정보가 시스템에 존재하지 않습니다.'); history.back();</script>"
            
        landlord_id = building['landlord_id']

        # 3. repair_logs에 데이터 삽입! (landlord_id 포함 ✅)
        cursor.execute("""
            INSERT INTO repair_logs 
            (tenant_id, landlord_id, room_number, problem_name, ai_estimated_cost, status) 
            VALUES (%s, %s, %s, %s, %s, '요청됨')
        """, (tenant_id, landlord_id, room_number, problem_name, estimated_cost))
        
        conn.commit()
        return "<script>alert('임대인에게 성공적으로 수리 요청이 전송되었습니다!\\n임대인이 확인 후 조치할 예정입니다.'); location.href='/myinfo';</script>"
        
    except Exception as e:
        print(f"❌ 임대인 수리 요청 DB 에러: {e}")
        return "<script>alert('요청 처리 중 오류가 발생했습니다.'); history.back();</script>"
    finally:
        cursor.close()
        conn.close()

# ✨ [신규] 임대인이 특정 수리 건을 업체(전문가)에게 배정하는 기능
@app.route('/landlord/assign_expert', methods=['POST'])
def assign_expert():
    if 'userid' not in session or session.get('role') != 'landlord':
        return redirect('/login')

    repair_id = request.form.get('repair_id')
    expert_id = request.form.get('expert_id')

    if not expert_id:
        return "<script>alert('업체를 선택해주세요.'); history.back();</script>"

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 수리 내역(repair_logs)에 전문가 아이디를 넣고, 상태를 '수리중'으로 변경
        cursor.execute("""
            UPDATE repair_logs 
            SET expert_id = %s, status = '수리중' 
            WHERE id = %s AND landlord_id = %s
        """, (expert_id, repair_id, session['userid']))
        conn.commit()
        return "<script>alert('해당 업체로 수리 배정이 완료되었습니다!'); location.href='/landlord_dashboard';</script>"
    except Exception as e:
        print(f"❌ 업체 배정 오류: {e}")
        return "<script>alert('배정 중 오류가 발생했습니다.'); history.back();</script>"
    finally:
        cursor.close(); conn.close()

# ==========================================
# ✨ [결제 시스템] 카카오페이 · 토스페이먼츠 · 포인트충전 · 구독
# ==========================================

def generate_order_id(prefix="ORD"):
    """충돌 없는 고유 주문 ID 생성"""
    return f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"


def _process_payment_success(cursor, payment):
    """
    결제 완료 후 목적에 따라 포인트 지급 / 예약 확정 / 구독 처리
    cursor: DB 커서 (conn.commit()은 외부에서 수행)
    payment: payments 테이블 row (dict)
    """
    userid       = payment['userid']
    purpose      = payment['purpose']
    point_amount = payment.get('point_amount', 0)
    amount       = payment.get('amount', 0)
    ref_id       = payment.get('ref_id')

    if purpose == 'point_charge':
        cursor.execute(
            "UPDATE users SET points = IFNULL(points, 0) + %s WHERE userid = %s",
            (point_amount, userid)
        )
        cursor.execute(
            "INSERT INTO point_history (userid, amount, description) VALUES (%s, %s, %s)",
            (userid, point_amount, f"포인트 충전 ({amount:,}원)")
        )

    elif purpose == 'expert_reserve' and ref_id:
        cursor.execute(
            "UPDATE reservations SET status = '결제완료' WHERE id = %s AND userid = %s",
            (ref_id, userid)
        )

    elif purpose == 'repair_service' and ref_id:
        cursor.execute(
            "UPDATE repair_logs SET status = '결제완료', actual_cost = %s WHERE id = %s",
            (amount, ref_id)
        )

    elif purpose == 'subscription':
        plan  = 'basic' if amount < 20000 else 'premium'
        start = datetime.now().date()
        end   = start + timedelta(days=30)
        cursor.execute('''
            INSERT INTO subscriptions (userid, plan, start_date, end_date, status)
            VALUES (%s, %s, %s, %s, 'active')
            ON DUPLICATE KEY UPDATE plan = VALUES(plan), start_date = VALUES(start_date),
                                    end_date = VALUES(end_date), status = 'active'
        ''', (userid, plan, start, end))


# --- [포인트 충전 페이지] ---
@app.route('/payment/charge')
def payment_charge():
    if 'user' not in session:
        return redirect('/login')

    # 사업성 고려한 현실적인 충전 옵션 세팅
    charge_options = [
        {'amount': 10000,  'points': 10000},  # 기본
        {'amount': 30000,  'points': 31000},  # +1,000P
        {'amount': 50000,  'points': 52000},  # +3,000P
        {'amount': 100000, 'points': 105000}  # +10,000P (최대 10%)
    ]

    return render_template('payment_charge.html', 
                           user_info=session['user'],
                           charge_options=charge_options,
                           toss_client_key=TOSS_CLIENT_KEY,
                           base_url=BASE_URL)


# --- [카카오페이] 결제 준비 ---
@app.route('/payment/kakao/ready', methods=['POST'])
def kakao_pay_ready():
    if 'user' not in session:
        return jsonify({"result": "fail", "message": "로그인이 필요합니다."}), 401

    data         = request.get_json()
    purpose      = data.get('purpose', 'point_charge')
    amount       = int(data.get('amount', 0))
    point_amount = int(data.get('point_amount', amount))
    ref_id       = data.get('ref_id')
    userid       = session['user']['userid']
    order_id     = generate_order_id("KKO")

    if amount <= 0:
        return jsonify({"result": "fail", "message": "결제 금액이 올바르지 않습니다."}), 400

    item_name_map = {
        "point_charge":   f"HomeFix 포인트 {point_amount:,}P 충전",
        "expert_reserve": "전문가 예약",
        "repair_service": "수리 서비스 결제",
        "subscription":   "HomeFix 구독권",
    }
    item_name = item_name_map.get(purpose, "HomeFix 결제")

    headers = {
        "Authorization": f"KakaoAK {KAKAO_ADMIN_KEY}",
        "Content-type":  "application/x-www-form-urlencoded;charset=utf-8",
    }
    params = {
        "cid":              "TC0ONETIME",
        "partner_order_id": order_id,
        "partner_user_id":  userid,
        "item_name":        item_name,
        "quantity":         1,
        "total_amount":     amount,
        "vat_amount":       amount // 11,
        "tax_free_amount":  0,
        "approval_url":     f"{BASE_URL}/payment/kakao/success?order_id={order_id}",
        "cancel_url":       f"{BASE_URL}/payment/kakao/cancel?order_id={order_id}",
        "fail_url":         f"{BASE_URL}/payment/kakao/fail?order_id={order_id}",
    }

    try:
        res = http_requests.post(
            "https://kapi.kakao.com/v1/payment/ready",
            headers=headers, data=params, timeout=10
        )
        res.raise_for_status()
        kakao_data = res.json()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO payments (userid, order_id, payment_type, purpose, amount, point_amount, status, pg_tid, ref_id)
            VALUES (%s, %s, 'kakao', %s, %s, %s, '대기', %s, %s)
        ''', (userid, order_id, purpose, amount, point_amount, kakao_data.get('tid', ''), ref_id))
        conn.commit()
        cursor.close(); conn.close()

        session['kakao_tid']      = kakao_data['tid']
        session['kakao_order_id'] = order_id
        session.modified = True

        return jsonify({
            "result": "success",
            "next_redirect_pc_url":     kakao_data.get('next_redirect_pc_url'),
            "next_redirect_mobile_url": kakao_data.get('next_redirect_mobile_url'),
            "order_id": order_id,
        })

    except Exception as e:
        print(f"❌ 카카오페이 준비 오류: {e}")
        return jsonify({"result": "fail", "message": "결제 준비 중 오류가 발생했습니다."}), 500


# --- [카카오페이] 결제 성공 콜백 ---
@app.route('/payment/kakao/success')
def kakao_pay_success():
    if 'user' not in session:
        return redirect('/login')

    pg_token = request.args.get('pg_token')
    order_id = request.args.get('order_id', session.get('kakao_order_id', ''))
    tid      = session.get('kakao_tid', '')
    userid   = session['user']['userid']

    if not pg_token or not tid:
        return "<script>alert('결제 정보가 올바르지 않습니다.'); location.href='/payment/charge';</script>"

    headers = {
        "Authorization": f"KakaoAK {KAKAO_ADMIN_KEY}",
        "Content-type":  "application/x-www-form-urlencoded;charset=utf-8",
    }
    params = {
        "cid":              "TC0ONETIME",
        "tid":              tid,
        "partner_order_id": order_id,
        "partner_user_id":  userid,
        "pg_token":         pg_token,
    }

    try:
        res = http_requests.post(
            "https://kapi.kakao.com/v1/payment/approve",
            headers=headers, data=params, timeout=10
        )
        res.raise_for_status()

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM payments WHERE order_id = %s AND userid = %s", (order_id, userid))
        payment = cursor.fetchone()

        if not payment:
            cursor.close(); conn.close()
            return "<script>alert('결제 정보를 찾을 수 없습니다.'); location.href='/';</script>"

        cursor.execute("UPDATE payments SET status = '완료' WHERE order_id = %s", (order_id,))
        _process_payment_success(cursor, payment)
        conn.commit()

        if payment['purpose'] == 'point_charge':
            session['user']['points'] = session['user'].get('points', 0) + payment['point_amount']
            session.modified = True

        cursor.close(); conn.close()
        return redirect('/payment/success?order_id=' + order_id)

    except Exception as e:
        print(f"❌ 카카오페이 승인 오류: {e}")
        return "<script>alert('결제 승인 중 오류가 발생했습니다.'); location.href='/payment/charge';</script>"


@app.route('/payment/kakao/cancel')
def kakao_pay_cancel():
    order_id = request.args.get('order_id', '')
    if order_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE payments SET status = '취소' WHERE order_id = %s", (order_id,))
        conn.commit()
        cursor.close(); conn.close()
    return "<script>alert('결제가 취소되었습니다.'); location.href='/payment/charge';</script>"


@app.route('/payment/kakao/fail')
def kakao_pay_fail():
    order_id = request.args.get('order_id', '')
    if order_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE payments SET status = '실패' WHERE order_id = %s", (order_id,))
        conn.commit()
        cursor.close(); conn.close()
    return "<script>alert('결제에 실패했습니다. 다시 시도해주세요.'); location.href='/payment/charge';</script>"


# --- [토스페이먼츠] 결제 승인 ---
@app.route('/payment/toss/confirm', methods=['POST'])
def toss_pay_confirm():
    if 'user' not in session:
        return jsonify({"result": "fail", "message": "로그인이 필요합니다."}), 401

    data         = request.get_json()
    payment_key  = data.get('paymentKey')
    order_id     = data.get('orderId')
    amount       = int(data.get('amount', 0))
    point_amount = int(data.get('point_amount', amount))
    purpose      = data.get('purpose', 'point_charge')
    ref_id       = data.get('ref_id')
    userid       = session['user']['userid']

    if not payment_key or not order_id:
        return jsonify({"result": "fail", "message": "결제 정보가 누락되었습니다."}), 400

    import base64 as b64
    secret_b64 = b64.b64encode(f"{TOSS_SECRET_KEY}:".encode()).decode()
    headers = {
        "Authorization": f"Basic {secret_b64}",
        "Content-Type":  "application/json",
    }
    body = {"paymentKey": payment_key, "orderId": order_id, "amount": amount}

    try:
        res = http_requests.post(
            "https://api.tosspayments.com/v1/payments/confirm",
            headers=headers, json=body, timeout=10
        )
        res.raise_for_status()
        toss_data = res.json()

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('''
            INSERT INTO payments (userid, order_id, payment_type, purpose, amount, point_amount, status, pg_tid, ref_id)
            VALUES (%s, %s, 'toss', %s, %s, %s, '완료', %s, %s)
            ON DUPLICATE KEY UPDATE status = '완료', pg_tid = VALUES(pg_tid)
        ''', (userid, order_id, purpose, amount, point_amount, toss_data.get('paymentKey', ''), ref_id))

        cursor.execute("SELECT * FROM payments WHERE order_id = %s AND userid = %s", (order_id, userid))
        payment = cursor.fetchone()
        _process_payment_success(cursor, payment)
        conn.commit()

        if purpose == 'point_charge':
            session['user']['points'] = session['user'].get('points', 0) + point_amount
            session.modified = True

        cursor.close(); conn.close()
        return jsonify({"result": "success", "order_id": order_id})

    except Exception as e:
        print(f"❌ 토스 결제 승인 오류: {e}")
        return jsonify({"result": "fail", "message": "결제 승인 중 오류가 발생했습니다."}), 500


@app.route('/payment/toss/success')
def toss_success():
    payment_key = request.args.get('paymentKey')
    order_id = request.args.get('orderId')
    amount = request.args.get('amount')
    purpose = request.args.get('purpose')
    repair_id_raw = request.args.get('ref_id') # URL에서 받은 원본 값

    print(f"🔍 결제 성공 데이터 확인 -> 목적: {purpose}, 수리ID: {repair_id_raw}")

    if purpose == 'repair_service' and repair_id_raw:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # ✨ 핵심: ID를 안전하게 숫자로 변환합니다.
            repair_id = int(repair_id_raw)
            
            # ✨ paid_at 컬럼이 없으므로 status만 업데이트하도록 수정했습니다.
            update_query = """
                UPDATE repair_logs 
                SET status = '결제완료' 
                WHERE id = %s
            """
            cursor.execute(update_query, (repair_id,))
            conn.commit()
            
            # 쿼리가 실제로 몇 줄이나 영향을 줬는지 확인 (0이면 ID가 틀린 것)
            if cursor.rowcount > 0:
                print(f"✅ [HomeFix] DB 업데이트 성공! 수리 ID {repair_id} -> 결제완료")
            else:
                print(f"⚠️ [HomeFix] 업데이트 실패: ID {repair_id}와 일치하는 데이터가 없습니다.")

        except Exception as e:
            conn.rollback()
            print(f"❌ [HomeFix] DB 업데이트 에러 발생: {e}")
        finally:
            cursor.close()
            conn.close()

    return render_template('payment_success.html', 
                           message="수리비 결제가 성공적으로 완료되었습니다!")

@app.route('/payment/toss/fail')
def toss_pay_fail_redirect():
    error_msg = request.args.get('message', '결제에 실패했습니다.')
    return f"<script>alert('{error_msg}'); location.href='/payment/charge';</script>"


# --- [환불 처리] ---
@app.route('/payment/refund', methods=['POST'])
def payment_refund():
    if 'user' not in session:
        return jsonify({"result": "fail", "message": "로그인이 필요합니다."}), 401

    data     = request.get_json()
    order_id = data.get('order_id')
    userid   = session['user']['userid']

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM payments WHERE order_id = %s", (order_id,))
    payment = cursor.fetchone()

    is_admin = session['user'].get('role') == 'admin'
    if not payment:
        cursor.close(); conn.close()
        return jsonify({"result": "fail", "message": "결제 내역을 찾을 수 없습니다."}), 404
    if payment['userid'] != userid and not is_admin:
        cursor.close(); conn.close()
        return jsonify({"result": "fail", "message": "권한이 없습니다."}), 403
    if payment['status'] != '완료':
        cursor.close(); conn.close()
        return jsonify({"result": "fail", "message": "환불 가능한 결제가 아닙니다."}), 400

    try:
        if payment['payment_type'] == 'kakao':
            headers = {
                "Authorization": f"KakaoAK {KAKAO_ADMIN_KEY}",
                "Content-type": "application/x-www-form-urlencoded;charset=utf-8",
            }
            http_requests.post(
                "https://kapi.kakao.com/v1/payment/cancel",
                headers=headers,
                data={"cid": "TC0ONETIME", "tid": payment['pg_tid'],
                      "cancel_amount": payment['amount'], "cancel_tax_free_amount": 0},
                timeout=10
            ).raise_for_status()

        elif payment['payment_type'] == 'toss':
            import base64 as b64
            secret_b64 = b64.b64encode(f"{TOSS_SECRET_KEY}:".encode()).decode()
            http_requests.post(
                f"https://api.tosspayments.com/v1/payments/{payment['pg_tid']}/cancel",
                headers={"Authorization": f"Basic {secret_b64}", "Content-Type": "application/json"},
                json={"cancelReason": "고객 요청 환불", "cancelAmount": payment['amount']},
                timeout=10
            ).raise_for_status()

        cursor.execute("UPDATE payments SET status = '환불' WHERE order_id = %s", (order_id,))

        if payment['purpose'] == 'point_charge':
            cursor.execute(
                "UPDATE users SET points = GREATEST(0, IFNULL(points, 0) - %s) WHERE userid = %s",
                (payment['point_amount'], payment['userid'])
            )
            cursor.execute(
                "INSERT INTO point_history (userid, amount, description) VALUES (%s, %s, %s)",
                (payment['userid'], -payment['point_amount'], f"결제 환불 포인트 차감 (주문: {order_id})")
            )

        conn.commit()
        cursor.close(); conn.close()
        return jsonify({"result": "success", "message": "환불이 완료되었습니다."})

    except Exception as e:
        print(f"❌ 환불 오류: {e}")
        cursor.close(); conn.close()
        return jsonify({"result": "fail", "message": "환불 처리 중 오류가 발생했습니다."}), 500


# --- [결제 완료 페이지] ---
@app.route('/payment/success')
def payment_success_page():
    if 'user' not in session:
        return redirect('/login')
    order_id = request.args.get('order_id', '')
    payment  = None
    if order_id:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM payments WHERE order_id = %s AND userid = %s",
            (order_id, session['user']['userid'])
        )
        payment = cursor.fetchone()
        cursor.close(); conn.close()
    return render_template('payment_success.html', payment=payment, user_info=session['user'])


# --- [내 결제 내역] ---
@app.route('/payment/history')
def payment_history():
    if 'user' not in session:
        return redirect('/login')
    userid = session['user']['userid']
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM payments WHERE userid = %s ORDER BY created_at DESC LIMIT 50",
        (userid,)
    )
    payments = cursor.fetchall()
    cursor.close(); conn.close()
    for p in payments:
        if isinstance(p.get('created_at'), datetime):
            p['created_at'] = p['created_at'].strftime('%Y-%m-%d %H:%M')
    return render_template('payment_history.html', payments=payments, user_info=session['user'])


# --- [전문가 예약 + 결제 연동] ---
@app.route('/payment/reserve_with_payment', methods=['POST'])
def reserve_with_payment():
    if 'user' not in session:
        return "<script>alert('로그인이 필요합니다.'); location.href='/login';</script>"

    expert_name  = request.form.get('expert_name', '')
    use_points   = int(request.form.get('use_points', 0))
    pay_amount   = int(request.form.get('pay_amount', 0))
    payment_type = request.form.get('payment_type', 'kakao')
    userid       = session['user']['userid']
    cur_points   = session['user'].get('points', 0)

    if use_points > cur_points:
        return "<script>alert('보유 포인트가 부족합니다.'); history.back();</script>"

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO reservations (userid, expert_name, used_points, status)
            VALUES (%s, %s, %s, '결제대기')
        ''', (userid, expert_name, use_points))
        conn.commit()
        reservation_id = cursor.lastrowid

        if use_points > 0:
            cursor.execute(
                "UPDATE users SET points = IFNULL(points, 0) - %s WHERE userid = %s",
                (use_points, userid)
            )
            cursor.execute(
                "INSERT INTO point_history (userid, amount, description) VALUES (%s, %s, %s)",
                (userid, -use_points, f"{expert_name} 예약 포인트 사용")
            )
            session['user']['points'] = cur_points - use_points
            session.modified = True
            conn.commit()

        if pay_amount <= 0:
            cursor.execute(
                "UPDATE reservations SET status = '예약완료' WHERE id = %s", (reservation_id,)
            )
            conn.commit()
            cursor.close(); conn.close()
            return f"<script>alert('예약이 완료되었습니다! (포인트 {use_points:,}P 사용)'); location.href='/myinfo';</script>"

        cursor.close(); conn.close()
        return render_template(
            'payment_reserve_confirm.html',
            expert_name=expert_name,
            use_points=use_points,
            pay_amount=pay_amount,
            payment_type=payment_type,
            reservation_id=reservation_id,
            toss_client_key=TOSS_CLIENT_KEY,
            kakao_js_key=KAKAO_JS_KEY,
            user_info=session['user']
        )

    except Exception as e:
        print(f"❌ 예약 결제 오류: {e}")
        conn.rollback()
        cursor.close(); conn.close()
        return "<script>alert('예약 중 오류가 발생했습니다.'); history.back();</script>"


# --- [관리자] 결제 현황 대시보드 ---
@app.route('/admin/payments')
def admin_payment_dashboard():
    if 'user' not in session or session['user'].get('role') != 'admin':
        return redirect('/login')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute('''
        SELECT p.*, u.name as user_name
        FROM payments p LEFT JOIN users u ON p.userid = u.userid
        ORDER BY p.created_at DESC LIMIT 100
    ''')
    all_payments = cursor.fetchall()

    cursor.execute("SELECT SUM(amount) as total, COUNT(*) as cnt FROM payments WHERE status = '완료'")
    summary = cursor.fetchone()

    cursor.execute('''
        SELECT purpose, COUNT(*) as cnt, SUM(amount) as total
        FROM payments WHERE status = '완료' GROUP BY purpose
    ''')
    by_purpose = cursor.fetchall()
    cursor.close(); conn.close()

    for p in all_payments:
        if isinstance(p.get('created_at'), datetime):
            p['created_at'] = p['created_at'].strftime('%Y-%m-%d %H:%M')

    return render_template(
        'admin_payments.html',
        payments=all_payments,
        summary=summary,
        by_purpose=by_purpose,
        user_info=session['user']
    )


# =====================================================
# [포트원 V2] 임대인 수리비 결제 완료 검증 라우트
# landlord_repairs.html의 requestTossPayment() 에서 호출
# =====================================================
@app.route('/payment/complete', methods=['POST'])
def payment_complete():
    if 'user' not in session:
        return jsonify({'status': 'error', 'message': '로그인이 필요합니다.'}), 401

    data      = request.get_json()
    imp_uid   = data.get('imp_uid')    # 포트원 결제 ID (paymentId)
    repair_id = data.get('repair_id')
    amount    = data.get('amount')

    if not imp_uid or not repair_id or not amount:
        return jsonify({'status': 'error', 'message': '필수 파라미터가 누락되었습니다.'})

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # 1. 포트원 V2 서버 API로 결제 검증
        portone_secret = os.environ.get('PORTONE_API_SECRET', '')
        verify_res = http_requests.get(
            f'https://api.portone.io/payments/{imp_uid}',
            headers={'Authorization': f'PortOne {portone_secret}'}
        )
        payment_data = verify_res.json()

        paid_amount = payment_data.get('amount', {}).get('paid', 0)
        pay_status  = payment_data.get('status', '')

        # 2. 금액 위변조 검증
        if pay_status != 'PAID' or int(paid_amount) != int(amount):
            return jsonify({
                'status': 'error',
                'message': f'결제 검증 실패 (서버금액: {paid_amount}, 요청금액: {amount})'
            })

        # 3. repair_logs 상태 업데이트
        cursor.execute("""
            UPDATE repair_logs
            SET status = '결제완료', actual_cost = %s
            WHERE id = %s AND landlord_id = %s
        """, (amount, repair_id, session['user']['userid']))

        conn.commit()
        return jsonify({'status': 'success', 'message': '결제 완료 처리되었습니다.'})

    except Exception as e:
        conn.rollback()
        print(f'[payment/complete] 오류: {e}')
        return jsonify({'status': 'error', 'message': str(e)})

    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    # 메인 포트인 5000에서 통합 실행됩니다.
    app.run(debug=True, host='0.0.0.0', port=5000)