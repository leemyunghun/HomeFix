import os
import json
import pandas as pd
import mysql.connector
import uuid
from mysql.connector import Error
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime, timedelta
from openai import OpenAI
from supabase import create_client
from dotenv import load_dotenv
from flask import jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import base64
from io import BytesIO

# .env 파일 로드
load_dotenv()

# ✨ 통합: 기본 templates 폴더 하나만 사용합니다.
app = Flask(__name__)

# 보안 및 API 설정
app.secret_key = os.getenv("FLASK_SECRET_KEY", "home_fix_fallback_key")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Supabase 설정
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# 카카오 JavaScript 키
KAKAO_JS_KEY = os.getenv("KAKAO_JS_KEY", "")

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
        conn = mysql.connector.connect(**TIDB_CONFIG)
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
        
        try:
            cursor.execute("ALTER TABLE history ADD COLUMN risk_level INT DEFAULT 1")
            cursor.execute("ALTER TABLE history ADD COLUMN warning TEXT")
        except: pass

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN points INT DEFAULT 0")
        except: 
            pass

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
            # 현재 시간을 DB에 기록
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
        from werkzeug.utils import secure_filename
        filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{image_file.filename}")
        upload_path = os.path.join('static', 'uploads', 'reviews')
        if not os.path.exists(upload_path): os.makedirs(upload_path)
        image_file.save(os.path.join(upload_path, filename))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. 리뷰 데이터 저장
    cursor.execute('''INSERT INTO reviews (userid, contractor_name, repair_item, cost, rating, comment, image_path)
                      VALUES (%s, %s, %s, %s, %s, %s, %s)''', 
                   (session['user']['userid'], contractor, item, cost, rating, comment, filename))
    
    # ✨ 2. 리뷰 작성 보상 포인트 지급 (1,000 포인트)
    reward_points = 1000
    cursor.execute('UPDATE users SET points = points + %s WHERE userid = %s', (reward_points, session['user']['userid']))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    # ✨ 3. 화면에 바로 반영되도록 현재 로그인 세션 정보 업데이트
    if 'points' in session['user']:
        session['user']['points'] += reward_points
    else:
        session['user']['points'] = reward_points
    session.modified = True
    
    # 4. AI 학습 진행
    try:
        sync_review_to_ai({'contractor': contractor, 'item': item, 'cost': cost, 'rating': rating, 'comment': comment})
    except Exception as e: print(f"⚠️ AI 학습 실패: {e}")
    
    # ✨ 5. 단순 redirect 대신, 포인트 지급 완료 알림창을 띄우고 리뷰 페이지로 이동
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
    
    expert_name = request.form.get('expert_name') # 어떤 업체인지 받아오기
    raw_points = request.form.get('use_points', '0')
    use_points = int(raw_points) if raw_points.isdigit() else 0
    current_points = session['user'].get('points', 0)
    
    if use_points > current_points:
        return "<script>alert('보유 포인트가 부족합니다.'); history.back();</script>"
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # ✨ 1. 예약 내역을 DB에 드디어 저장합니다!
    cursor.execute("INSERT INTO reservations (userid, expert_name, used_points) VALUES (%s, %s, %s)", 
                   (session['user']['userid'], expert_name, use_points))
    
    # 2. 포인트 차감
    cursor.execute("UPDATE users SET points = points - %s WHERE userid = %s", (use_points, session['user']['userid']))
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
    user_input, image_file = request.form.get('problem', '').strip(), request.files.get('image')
    has_image = image_file and image_file.filename != ''
    if not user_input and not has_image: return jsonify({"status": "invalid", "message": "증상이나 사진을 첨부해주세요."}), 400

    try:
        context, sources = "관련 매뉴얼 내용을 찾을 수 없습니다.", ["일반 지식"]
        if user_input:
            emb_res = client.embeddings.create(input=user_input.replace("\n", " "), model="text-embedding-3-small")
            rpc_res = supabase.rpc("match_documents", {"query_embedding": emb_res.data[0].embedding, "match_threshold": 0.25, "match_count": 6}).execute()
            if rpc_res.data:
                documents = rpc_res.data
                sources = list(set([doc.get('metadata', {}).get('source', '일반 지식') for doc in documents]))
                context = "\n\n".join([f"[{doc['metadata'].get('source')}] {doc['content']}" for doc in documents])

        ai_query = user_input if user_input else "사진 속의 집수리 문제를 분석하고 해결책을 제시해줘."
        
        # ✨ 수정된 부분: AI 프롬프트를 전문적이고 체계적으로 고도화
        text_content = f"""당신은 20년 경력의 베테랑 집수리 및 설비 전문 AI '홈픽스'입니다.
사용자의 질문이나 사진을 분석하여 원인과 해결책을 정확한 JSON 형태로 진단해 주세요.

[🛠️ 진단 영역 및 예외 처리 가이드]
1. 허용 (집수리 영역)
   - 배관/설비(변기 막힘, 싱크대 막힘, 누수 등), 인테리어, 가구 조립, 공구 사용법, 전기/조명 등 주거 공간의 유지보수와 관련된 모든 문제.
   - 🚨 핵심 규칙: "화장실 변기가 막혔어요", "물이 안 내려가요", "물이 새요"와 같이 짧은 일상적인 증상 호소라도 완벽한 '집수리 영역'입니다. 절대 진단을 거절하지 말고, 가장 흔한 원인과 해결책(예: 뚫어뻥/관통기 사용법 등)을 추론하여 진단하세요.

2. 차단 (비관련 영역)
   - 프로그래밍, IT 기술, 단순 인사말(안녕), 정치, 경제, 요리 등 집수리와 완전히 무관한 주제.
   - 차단 대상일 경우 무조건 아래 JSON 형태로만 응답하세요:
   {{"problem_name": "진단 불가", "risk_level": 1, "estimated_cost": "-", "warning": "저는 집수리 전문 AI입니다. 주거 공간의 고장, 수리, 인테리어와 관련된 질문만 답변이 가능합니다.", "steps": ["입력하신 내용은 집수리와 관련이 없습니다.", "수리가 필요한 문제나 사진을 다시 입력해 주세요."], "tools": [], "sources": ""}}

[학습 지식]
{context}

[사용자 입력]
{ai_query}

위 가이드를 엄격히 준수하여, 사용자 입력이 '허용' 영역일 경우 아래의 [정상 답변 형식 JSON]에 맞춰 구체적이고 전문적인 답변을 생성하세요.

[정상 답변 형식 JSON]
{{"problem_name": "문제 제목 (예: 화장실 변기 막힘)", "risk_level": 1, "estimated_cost": "예상 비용 (예: 셀프 0원 / 전문가 5~10만원)", "warning": "작업 시 주의사항 (예: 무리한 힘을 가하면 배관이 파손될 수 있습니다.)", "steps": ["해결을 위한 상세 단계 1", "단계 2", "단계 3"], "tools": ["필요한 도구명 1", "도구명 2"], "sources": "{', '.join(sources)}"}}"""

        messages_content = [{"type": "text", "text": text_content}]
        if has_image:
            base64_image = encode_image(image_file)
            if base64_image: messages_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}})

        response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": messages_content}], response_format={"type": "json_object"})
        result = json.loads(response.choices[0].message.content)
        result['risk_level'] = result.get('risk_level', 1)
        result['warning'] = result.get('warning', '주의사항 없음')

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
        uid, pw = request.form.get('userid'), request.form.get('password')
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM users WHERE userid = %s', (uid,))
        user = cursor.fetchone()
        cursor.close(); conn.close()
        
        if user and check_password_hash(user['password'], pw):
            # 💡 기존 로직: 탈퇴된 계정인지 확인
            if user.get('status') == 'Deleted':
                return "<script>alert('탈퇴 처리된 계정입니다.'); history.back();</script>"
            
            # ✨ 신규 로직: 업체/임대인 가입 승인 대기 상태 확인
            if user.get('approval_status') == 'pending':
                return "<script>alert('가입 서류 심사 중입니다. 관리자 승인 후 서비스 이용이 가능합니다.'); history.back();</script>"
                
            session['user'] = user
            return redirect(url_for('index'))
            
        return "<script>alert('틀린 정보입니다.'); history.back();</script>"
    return render_template('login.html')

# ✨ 파일이 저장될 폴더 경로 설정 (app.py 상단 라우트들 위에 적어주세요)
UPLOAD_FOLDER = 'static/uploads/documents'
os.makedirs(UPLOAD_FOLDER, exist_ok=True) # 폴더가 없으면 자동으로 생성

# ==========================================
# 관리자: 가입 승인 처리 API
# ==========================================
@app.route('/admin/approve_user/<userid>', methods=['POST'])
def approve_user(userid):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # DB의 approval_status를 'pending'에서 'approved'로 변경합니다.
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
        
        # 서류 미비 등으로 거절 시 해당 계정을 DB에서 완전히 삭제합니다.
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
        f = request.files # ✨ 폼에서 전송된 파일 데이터 가져오기
        
        hashed_pw = generate_password_hash(d['password'])
        role = d.get('role', 'user') # 역할 가져오기 (기본값 'user')
        
        # 💡 핵심: 역할에 따른 승인 상태 설정
        approval_status = 'pending' if role in ['expert', 'landlord'] else 'approved'
        
        # 안전하게 파일을 저장하고 경로를 반환하는 내부 함수
        def save_file(file_obj, userid):
            if file_obj and file_obj.filename:
                ext = file_obj.filename.rsplit('.', 1)[-1].lower()
                # 해킹 방지 및 중복 방지를 위해 고유한 파일명 생성 (예: myid_a1b2c3d4.jpg)
                filename = f"{userid}_{uuid.uuid4().hex[:8]}.{ext}"
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                file_obj.save(filepath)
                return f"/{filepath}" # DB에 저장될 웹 접근 경로 (/static/...)
            return None

        # 업로드된 파일들 저장
        idcard_path = save_file(f.get('idcard_file'), d['userid'])
        bizreg_path = save_file(f.get('bizreg_file'), d['userid'])
        estate_path = save_file(f.get('estate_file'), d['userid'])

        # 생년월일 빈 값 처리 (업체/임대인은 폼에서 생년월일이 없으므로 NULL 처리)
        birthdate = d.get('birthdate')
        if not birthdate:
            birthdate = None

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # ✨ DB INSERT 쿼리 확장
            cursor.execute('''
                INSERT INTO users 
                (name, userid, password, email, birthdate, phone, role, idcard_file, bizreg_file, estate_file, approval_status) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                d['name'], d['userid'], hashed_pw, d['email'], birthdate, d['phone'], 
                role, idcard_path, bizreg_path, estate_path, approval_status
            ))
            conn.commit()
            
            # 💡 가입 유형에 따라 알림 메시지 다르게 띄우기
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
        # 1. 아이디와 이메일로 유저 찾기
        cursor.execute('SELECT * FROM users WHERE userid = %s AND email = %s', (request.form.get('userid'), request.form.get('email')))
        user = cursor.fetchone()
        
        # ✨ 2. 핵심 수정: current_password 검사를 없애고, user가 존재하기만 하면 바로 통과!
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

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

@app.route('/result')
def result_page():
    res = session.get('last_result')
    if not res: return redirect('/')
    safe_data = {
        'problem_name': res.get('problem_name', '진단 결과'), 'steps': res.get('steps', []),
        'tools': res.get('tools', []), 'estimated_cost': res.get('estimated_cost', '비용 정보 없음'),
        'risk_level': res.get('risk_level', 1), 'warning': res.get('warning', '주의사항 없음')
    }
    return render_template('result.html', result_data=safe_data)

@app.route('/myinfo')
def myinfo():
    if 'user' not in session: 
        return redirect('/login')
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # ✨ 1. DB에서 현재 유저의 최신 정보를 다시 가져옵니다 (포인트 동기화 핵심!)
    cursor.execute("SELECT * FROM users WHERE userid = %s", (session['user']['userid'],))
    fresh_user_info = cursor.fetchone()
    
    # ✨ 2. 세션에 저장된 유저 정보도 최신 정보로 업데이트해 줍니다.
    session['user'] = fresh_user_info
    session.modified = True
    
    # 3. 내 진단 히스토리 가져오기
    cursor.execute('SELECT * FROM history WHERE userid = %s ORDER BY created_at DESC', (session['user']['userid'],))
    history = cursor.fetchall()
    
    # 4. 내가 쓴 후기 목록 가져오기 (수정 버튼용)
    cursor.execute('SELECT * FROM reviews WHERE userid = %s', (session['user']['userid'],))
    my_reviews = cursor.fetchall()
    reviews_dict = {r['repair_item']: r for r in my_reviews}
    
    cursor.close(); conn.close()
    
    # 렌더링할 때 fresh_user_info를 전달합니다.
    return render_template('myinfo.html', user_info=fresh_user_info, history=history, reviews_dict=reviews_dict)

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
    
    # 이미지가 새로 올라온 경우와 아닌 경우를 나눠서 업데이트
    if image_file and image_file.filename != '':
        from werkzeug.utils import secure_filename
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
    
    # 수정 시에는 포인트를 또 주면 안 되므로 포인트 지급 로직 생략!
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
    if not session.get('user'): return "Unauthorized", 401
    user_uid = session['user'].get('userid')
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM users WHERE id = %s', (session['user'].get('id'),))
        cursor.execute('DELETE FROM history WHERE userid = %s', (user_uid,))
        cursor.execute('DELETE FROM support WHERE userid = %s', (user_uid,))
        cursor.execute('DELETE FROM reviews WHERE userid = %s', (user_uid,))
        conn.commit(); session.clear()
        return "Success", 200
    except: return "Error", 500
    finally: cursor.close(); conn.close()

@app.route('/terms')
def terms(): return render_template('terms.html')

@app.route('/privacy')
def privacy(): return render_template('privacy.html')


# ==========================================
# [2] 👑 관리자(Admin) 전용 라우트 (통합됨)
# ==========================================

@app.route('/admin/dashboard')
def admin_dashboard_view():
    if session.get('user', {}).get('role') != 'admin': return "<script>alert('관리자 전용 페이지입니다.'); location.href='/';</script>"
    
    try:
        conn = get_db_connection()
        # 관리자 쿼리는 기존 fetchone()[0] 방식을 쓰기 위해 dictionary 옵션을 끕니다.
        cursor = conn.cursor() 
        
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM history")
        total_diagnoses = cursor.fetchone()[0]

        pending_experts = 0
        try:
            cursor.execute("SELECT COUNT(*) FROM experts WHERE status = 'Pending'")
            pending_experts = cursor.fetchone()[0]
        except: pass
        
        cursor.execute("SELECT problem_name, COUNT(*) FROM history GROUP BY problem_name LIMIT 5")
        stats = cursor.fetchall()
        labels = [s[0] for s in stats] if stats else ["데이터없음"]
        counts = [s[1] for s in stats] if stats else [0]
        
        cursor.close(); conn.close()
        
        return render_template('admin_dashboard.html', total_users=total_users, total_diagnoses=total_diagnoses, pending_support=pending_experts, labels=labels, counts=counts)
    except Exception as e:
        return f"대시보드 로드 실패: {e}"
    
@app.route('/admin/users')
def admin_user_management():
    if session.get('user', {}).get('role') != 'admin': return redirect('/')
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # ✨ 9시간 차이(UTC)를 보정하고, 상태를 판별하는 쿼리
        # 1. DATE_ADD(last_seen, INTERVAL 9 HOUR) : DB 시간을 한국 시간으로 변환해서 보여줌
        # 2. 5분(300초) 이상 차이 나면 '비활성화'
        query = """
            SELECT *, 
            DATE_ADD(last_seen, INTERVAL 9 HOUR) AS last_seen_kst,
            CASE 
                WHEN last_seen >= NOW() - INTERVAL 5 MINUTE THEN '온라인'
                ELSE '오프라인'
            END AS is_online 
            FROM users 
            ORDER BY id DESC
        """
        cursor.execute(query)
        users_list = cursor.fetchall()
        
        cursor.close(); conn.close()
        return render_template('admin_users.html', users=users_list)
    except Exception as e:
        return f"회원관리 로드 실패: {e}"

# ✨ [새로 추가] 관리자가 포인트를 수정했을 때 처리하는 라우트
@app.route('/admin/update_points', methods=['POST'])
def admin_update_points():
    if session.get('user', {}).get('role') != 'admin': 
        return "<script>alert('권한이 없습니다.'); history.back();</script>"
    
    try:
        target_userid = request.form.get('userid')
        new_points = int(request.form.get('points', 0))
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("UPDATE users SET points = %s WHERE userid = %s", (new_points, target_userid))
        conn.commit()
        
        cursor.close(); conn.close()
        
        return "<script>alert('회원 포인트가 성공적으로 변경되었습니다!'); history.back();</script>"
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
        
        # 💡 핵심: 상태를 'Deleted'로 바꾸어 즉시 로그인 불가 처리
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
        # 최신 예약이 위로 오도록 정렬
        cursor.execute("SELECT * FROM reservations ORDER BY created_at DESC")
        res_list = cursor.fetchall()
        cursor.close(); conn.close()
        
        return render_template('admin_reservations.html', reservations=res_list)
    except Exception as e:
        return f"예약 관리 로드 실패: {e}"
    
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
        
        # 1. 기존 예약 정보를 가져옵니다.
        cursor.execute("SELECT userid, used_points, status FROM reservations WHERE id = %s", (res_id,))
        res_info = cursor.fetchone()
        
        if not res_info:
            return jsonify({"result": "fail", "message": "예약 정보를 찾을 수 없습니다."})
            
        # 2. 상태 업데이트
        cursor.execute("UPDATE reservations SET status = %s WHERE id = %s", (new_status, res_id))
        
        # ✨ 3. [핵심] 예약취소로 바꿀 경우 포인트 환불 처리!
        # 기존 상태가 '예약취소'가 아닐 때만 환불 진행 (중복 환불 방지)
        if new_status == '예약취소' and res_info['status'] != '예약취소':
            
            # 💡 IFNULL(points, 0)을 사용해서, NULL값 에러를 원천 차단합니다!
            cursor.execute("UPDATE users SET points = IFNULL(points, 0) + %s WHERE userid = %s", 
                           (res_info['used_points'], res_info['userid']))
            
            # 만약 자기 자신의 예약을 취소하는 테스트 중이라면, 내 세션도 즉시 업데이트!
            if session['user']['userid'] == res_info['userid']:
                session['user']['points'] = session['user'].get('points', 0) + res_info['used_points']
                session.modified = True
                           
        conn.commit()
        cursor.close(); conn.close()
        
        return jsonify({"result": "success", "message": f"상태가 '{new_status}'(으)로 변경되었으며, 포인트가 환불되었습니다."})
    except Exception as e:
        return jsonify({"result": "fail", "message": str(e)})

if __name__ == '__main__':
    # 메인 포트인 5000에서 통합 실행됩니다.
    app.run(debug=True, host='0.0.0.0', port=5000)