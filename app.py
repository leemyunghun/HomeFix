import os
import json
import sqlite3
import math
import pandas as pd
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
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

print(f"🚀 HomeFix 서비스 가동 중... 카카오 키: {'로드 완료' if KAKAO_JS_KEY else '미설정'}")

# 후기 데이터를 AI 지식 베이스(Supabase)로 전송하여 학습시키는 함수
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

# ✅ 이미지를 GPT가 이해할 수 있는 Base64 형식으로 변환하는 함수
def encode_image_to_base64(image_file):
    return base64.b64encode(image_file.read()).decode('utf-8')

def init_db():
    import pandas as pd
    import json
    import os

    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    
    # 1. 모든 필요 테이블 생성 (하나라도 빠지면 홈페이지가 안 열림)
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, userid TEXT UNIQUE, 
                  password TEXT, email TEXT, birthdate TEXT, phone TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, userid TEXT, problem_name TEXT, 
                  steps TEXT, tools TEXT, estimated_cost TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS resources
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, name TEXT, 
                  location TEXT, contact TEXT, link TEXT, description TEXT, lat REAL, lon REAL)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS support
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, userid TEXT, title TEXT, content TEXT, 
                  status TEXT DEFAULT '접수완료', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    # 수리 후기 테이블 (사진 경로 포함)
    c.execute('''CREATE TABLE IF NOT EXISTS reviews
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  userid TEXT, 
                  contractor_name TEXT, 
                  repair_item TEXT, 
                  cost INTEGER, 
                  rating INTEGER, 
                  comment TEXT, 
                  image_path TEXT, 
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    # 2. 데이터 자동 로드 (파일이 있을 때만 실행)
    c.execute("SELECT COUNT(*) FROM resources")
    if c.fetchone()[0] == 0:
        print("🚀 데이터가 비어있어 파일 로드를 시도합니다...")
        
        # (1) 업체 CSV 로드
        contractor_path = os.path.join('homefix', '서울시 집수리 시공업체 정보.csv')
        if os.path.exists(contractor_path):
            try:
                # 인코딩 맞춰서 읽기
                for enc in ['cp949', 'utf-8-sig', 'utf-8']:
                    try:
                        df = pd.read_csv(contractor_path, encoding=enc)
                        for _, row in df.iterrows():
                            c.execute('INSERT INTO resources (category, name, location, contact, description) VALUES (?,?,?,?,?)',
                                     ('expert', str(row['업체명']), str(row['업체주소']), str(row['업체연락처']), f"시공분야: {row['주요시공분야']}"))
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
                            c.execute('INSERT INTO resources (category, name, location, contact, description) VALUES (?,?,?,?,?)',
                                     ('대여소', meta.get('name', '대여소'), meta.get('location', ''), meta.get('contact', ''), item.get('content', '')))
                print("✅ 대여소 데이터 로드 성공")
            except Exception as e: print(f"❌ JSON 로드 실패: {e}")

    conn.commit()
    conn.close()
    print("✨ DB 준비 완료!")

init_db()

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.context_processor
def inject_user():
    return dict(user_info=session.get('user'))

@app.route('/review')
def review_page():
    conn = get_db_connection()
    reviews = conn.execute('SELECT * FROM reviews ORDER BY created_at DESC').fetchall()
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
    
    # 이미지 파일 저장 로직
    filename = ""
    if image_file and image_file.filename != '':
        from werkzeug.utils import secure_filename
        from datetime import datetime
        filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{image_file.filename}")
        
        # static/uploads/reviews 폴더가 있어야 함
        upload_path = os.path.join('static', 'uploads', 'reviews')
        if not os.path.exists(upload_path):
            os.makedirs(upload_path)
        image_file.save(os.path.join(upload_path, filename))
    
    # 로컬 DB 저장
    conn = get_db_connection()
    conn.execute('''INSERT INTO reviews (userid, contractor_name, repair_item, cost, rating, comment, image_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''', 
                 (session['user']['userid'], contractor, item, cost, rating, comment, filename))
    conn.commit()
    conn.close()
    
    # AI 학습 연동 호출
    sync_review_to_ai({
        'contractor': contractor, 
        'item': item, 
        'cost': cost, 
        'rating': rating, 
        'comment': comment
    })
    
    return redirect(url_for('review_page'))

# --- 라우트 정의 ---

@app.route('/')
def index():
    conn = get_db_connection()
    # 최근 30일간의 후기 기반 평균 수리 비용 계산 (예: 수전)
    avg_price = conn.execute('SELECT AVG(cost) FROM reviews').fetchone()[0] or 0
    # 전체 학습된 지식(후기 + 기존지식) 개수
    total_knowledge = conn.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] + 1240
    conn.close()

    stats = {
        "avg_price": f"{int(avg_price):,}",
        "total_knowledge": total_knowledge,
        "recent_area": "성북구 안암동" # 예시
    }
    return render_template('index.html', stats=stats)

# --- 대여소 및 업체 검색 라우트 (중복되지 않게 이 코드로 교체하세요) ---

@app.route('/rental')
def rental_page():
    # 1. 사용자가 입력한 검색어 가져오기 (양쪽 공백 제거)
    query = request.args.get('query', '').strip()
    
    conn = get_db_connection()
    rentals = []

    try:
        # ✅ 기본 쿼리: '대여소' 카테고리인 데이터만 타겟팅
        sql = "SELECT * FROM resources WHERE category = '대여소'"
        
        if query:
            # 2. 검색어가 있을 경우: 위치(location), 이름(name), 설명(description) 중 포함 여부 확인
            # % 기호를 붙여 '성북'만 쳐도 '서울특별시 성북구'가 검색되게 함
            search_term = f"%{query}%"
            sql += " AND (location LIKE ? OR name LIKE ? OR description LIKE ?)"
            rentals_raw = conn.execute(sql, (search_term, search_term, search_term)).fetchall()
        else:
            # 3. 검색어가 없을 경우: 기본 15개 노출
            rentals_raw = conn.execute(sql + " LIMIT 15").fetchall()
        
        rentals = [dict(r) for r in rentals_raw]
        
    except Exception as e:
        print(f"❌ 대여소 검색 중 오류 발생: {e}")
    finally:
        conn.close()

    # 4. 결과 페이지로 데이터 전달
    return render_template('rental.html', rentals=rentals, query=query, kakao_js_key=KAKAO_JS_KEY)


@app.route('/expert')
def expert_matching():
    # 1. 검색어 가져오기
    query = request.args.get('query', '').strip()
    conn = get_db_connection()
    
    # 2. 업로드 코드(업체)에 맞춘 영문 카테고리 쿼리
    search_term = f"%{query}%"
    sql = "SELECT * FROM resources WHERE category = 'expert'"
    
    try:
        if query:
            # 업체명, 주소, 설명(시공분야) 검색
            sql += " AND (name LIKE ? OR location LIKE ? OR description LIKE ?) ORDER BY name ASC"
            experts_raw = conn.execute(sql, (search_term, search_term, search_term)).fetchall()
        else:
            # 기본 15개 노출
            experts_raw = conn.execute(sql + " LIMIT 15").fetchall()
            
        experts = [dict(row) for row in experts_raw]
    except Exception as e:
        print(f"❌ 업체 DB 조회 오류: {e}")
        experts = []
    finally:
        conn.close()

    return render_template('expert.html', experts=experts, query=query, kakao_js_key=KAKAO_JS_KEY)
# ✅ 이미지를 GPT가 이해할 수 있는 Base64 형식으로 변환하는 함수 (오류 방지 강화)
def encode_image(image_file):
    if not image_file or image_file.filename == '':
        return None
    try:
        # 파일 포인터를 처음으로 되돌려 읽기 오류 방지
        image_file.seek(0)
        return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        print(f"❌ 이미지 인코딩 실패: {e}")
        return None
@app.route('/diagnose', methods=['POST'])
def diagnose():
    if 'user' not in session:
        return jsonify({"status": "invalid", "message": "로그인이 필요합니다."}), 401

    user_input = request.form.get('problem', '')
    image_file = request.files.get('image') # index.html에서 보낸 사진 파일 받기

    try:
        # 1. 사용자 질문 벡터화 (RAG 검색용)
        emb_res = client.embeddings.create(input=user_input.replace("\n", " "), model="text-embedding-3-small")
        query_embedding = emb_res.data[0].embedding

        # 2. Supabase 통합 지식 검색
        rpc_res = supabase.rpc("match_documents", {
            "query_embedding": query_embedding,
            "match_threshold": 0.25,
            "match_count": 6
        }).execute()

        documents = rpc_res.data
        sources = list(set([doc.get('metadata', {}).get('source', '일반 지식') for doc in documents])) if documents else ["일반 지식"]
        context = "\n\n".join([f"[{doc['metadata'].get('source')}] {doc['content']}" for doc in documents]) if documents else "관련 매뉴얼 내용을 찾을 수 없습니다."

        # 3. GPT-4o-mini 멀티모달 콘텐츠 구성
        # 텍스트 정보(페르소나 + 지식 베이스 + 사용자 질문)
        text_content = f"""당신은 집수리 전문가 '홈픽스 삼촌'입니다. 
제공된 매뉴얼 데이터와 사용자가 보낸 사진(있을 경우)을 함께 분석하여 답변하세요.

[학습 지식 베이스]
{context}

[답변 지침]
1. 근거 명시: "서울시 매뉴얼을 보니", "사진을 분석해보니" 등의 표현을 사용하세요.
2. 사진 분석: 사진이 첨부되었다면 부식 상태, 균열 정도, 부품 명칭을 정확히 짚어주세요.
3. 안전/지원: 안전 주의사항과 서울시 지원 정책을 반드시 언급하세요.

[사용자 입력]
{user_input}

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

        # GPT에게 보낼 메시지 리스트 생성
        messages_content = [{"type": "text", "text": text_content}]

        # ✅ 4. 사진이 있다면 Base64로 인코딩하여 메시지에 추가
        if image_file:
            base64_image = encode_image(image_file)
            messages_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
            })

        # 5. GPT-4o-mini 호출 (Vision 기능 작동)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": messages_content}],
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        
        # 6. DB 저장 (기존 로직 유지)
        conn = get_db_connection()
        conn.execute(
            'INSERT INTO history (userid, problem_name, steps, tools, estimated_cost) VALUES (?, ?, ?, ?, ?)',
            (session['user']['userid'], result.get('problem_name', '진단 결과'), 
            json.dumps(result.get('steps', []), ensure_ascii=False),
            json.dumps(result.get('tools', []), ensure_ascii=False),
            result.get('estimated_cost', '비용 정보 없음'))
        )
        conn.commit()
        conn.close()
        
        session['last_result'] = result
        return jsonify(result)

    except Exception as e:
        print(f"❌ 진단 프로세스 오류: {e}")
        return jsonify({"status": "error", "message": "진단 중 오류가 발생했습니다."}), 500@app.route('/diagnose', methods=['POST'])
def diagnose():
    if 'user' not in session:
        return jsonify({"status": "invalid", "message": "로그인이 필요합니다."}), 401

    # 1. 입력값 받기
    user_input = request.form.get('problem', '').strip()
    image_file = request.files.get('image')
    has_image = image_file and image_file.filename != ''

    # ✅ 텍스트와 사진 둘 다 없는 경우 체크
    if not user_input and not has_image:
        return jsonify({"status": "invalid", "message": "증상을 설명하시거나 사진을 첨부해주세요."}), 400

    try:
        # 2. 텍스트가 있을 때만 벡터 검색(RAG) 수행
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

        # 3. 사진만 보냈을 경우를 위한 보완 질문 생성
        ai_query = user_input if user_input else "사진 속의 집수리 문제를 분석하고 해결책을 제시해줘."

        # 4. GPT-4o-mini 멀티모달 프롬프트 구성
        text_content = f"""당신은 집수리 전문가 '홈픽스 삼촌'입니다. 
사용자가 제공한 정보(텍스트/사진)를 바탕으로 전문적인 진단을 내려주세요.
초보자 맞춤 설명: '수전', '테프론 테이프' 같은 용어만 쓰지 말고, "수도꼭지 연결 부분", "하얀색 방수 테이프"처럼 누구나 알 수 있게 풀어서 설명하세요.

[학습 지식 베이스]
{context}

[답변 지침]
1. 사진 분석 우선: 사진이 있다면 사진 속 손상 부위, 부품 명칭, 노후 상태를 가장 먼저 언급하세요.
2. 텍스트 보완: 사용자의 설명이 없더라도 사진만 보고 예상되는 문제와 원인을 추론하세요.
3. 신뢰도: "사진을 보니 ~인 것 같구나"라는 삼촌 말투를 사용하고, 안전 주의사항을 꼭 포함하세요.

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

        # ✅ 사진 인코딩 및 추가
        if has_image:
            base64_image = encode_image(image_file)
            if base64_image:
                messages_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                })

        # 5. GPT-4o-mini 호출
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": messages_content}],
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        
        # 6. DB 저장 및 결과 반환 (기존과 동일)
        conn = get_db_connection()
        conn.execute(
            'INSERT INTO history (userid, problem_name, steps, tools, estimated_cost) VALUES (?, ?, ?, ?, ?)',
            (session['user']['userid'], result.get('problem_name', '사진 진단'), 
            json.dumps(result.get('steps', []), ensure_ascii=False),
            json.dumps(result.get('tools', []), ensure_ascii=False),
            result.get('estimated_cost', '비용 정보 없음'))
        )
        conn.commit()
        conn.close()
        
        session['last_result'] = result
        return jsonify(result)

    except Exception as e:
        print(f"❌ 진단 프로세스 오류: {e}")
        return jsonify({"status": "error", "message": "진단 중 오류가 발생했습니다."}), 500
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
# --- 아이디 찾기 ---
@app.route('/find-id', methods=['GET', 'POST'])
def find_id():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        
        conn = get_db_connection()
        user = conn.execute('SELECT userid FROM users WHERE name = ? AND email = ?', (name, email)).fetchone()
        conn.close()
        
        if user:
            # 실무에서는 개인정보 보호를 위해 일부 마스킹(예: ho***ix)을 하기도 합니다.
            return f"<script>alert('회원님의 아이디는 [{user['userid']}] 입니다.'); location.href='/login';</script>"
        else:
            return "<script>alert('일치하는 정보가 없습니다.'); history.back();</script>"
            
    return render_template('find_id.html')

# --- 비밀번호 재설정 ---
@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        uid = request.form.get('userid')
        name = request.form.get('name')
        email = request.form.get('email')
        new_pw = request.form.get('new_password')
        
        conn = get_db_connection()
        user = conn.execute('SELECT id FROM users WHERE userid = ? AND name = ? AND email = ?', 
                           (uid, name, email)).fetchone()
        
        if user:
            # 기존 회원가입 시 사용한 해싱 방식과 동일하게 저장
            hashed_pw = generate_password_hash(new_pw)
            conn.execute('UPDATE users SET password = ? WHERE id = ?', (hashed_pw, user['id']))
            conn.commit()
            conn.close()
            return "<script>alert('비밀번호가 성공적으로 변경되었습니다.'); location.href='/login';</script>"
        else:
            conn.close()
            return "<script>alert('정보가 일치하지 않습니다.'); history.back();</script>"
            
    return render_template('reset_password.html')

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