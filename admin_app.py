from flask import Flask, render_template, jsonify, request
import os
import mysql.connector
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

app = Flask(__name__, template_folder='admin_templates')

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("TIDB_HOST"),
        user=os.getenv("TIDB_USER"),
        password=os.getenv("TIDB_PASSWORD"),
        database="test", 
        port=4000
    )

@app.route('/')
def dashboard():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM history")
        total_diagnoses = cursor.fetchone()[0]

        pending_experts = 0
        try:
            cursor.execute("SELECT COUNT(*) FROM experts WHERE status = 'Pending'")
            pending_experts = cursor.fetchone()[0]
        except:
            pass
        
        cursor.execute("SELECT problem_name, COUNT(*) FROM history GROUP BY problem_name LIMIT 5")
        stats = cursor.fetchall()
        labels = [s[0] for s in stats] if stats else ["데이터없음"]
        counts = [s[1] for s in stats] if stats else [0]
        
        cursor.close()
        conn.close()
        return render_template('admin_dashboard.html', total_users=total_users, total_diagnoses=total_diagnoses, pending_support=pending_experts, labels=labels, counts=counts)
    except Exception as e:
        return f"대시보드 로드 실패: {e}"

# [수정] 이미지 속 실제 DB 컬럼 순서에 맞춰서 호출합니다.
@app.route('/users')
def user_management():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # 이미지 기준 순서: 0:id, 1:name, 2:userid, 3:password, 4:email, 5:birthdate, 6:phone, 7:status, 8:deleted_at, 9:role
        cursor.execute("SELECT id, name, userid, email, phone, status, deleted_at FROM users ORDER BY id DESC")
        users_list = cursor.fetchall()
        cursor.close()
        conn.close()
        return render_template('admin_users.html', users=users_list)
    except Exception as e:
        print(f"User Load Error: {e}")
        return f"회원관리 로드 실패: {e}"
    
# [추가] 회원 탈퇴 취소(복구) API
@app.route('/restore_user/<string:user_id>', methods=['POST'])
def restore_user(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # status를 Active로 바꾸고, 탈퇴 신청 시간을 다시 NULL로 초기화합니다.
        query = """
            UPDATE users 
            SET status = 'Active', deleted_at = NULL 
            WHERE userid = %s
        """
        cursor.execute(query, (user_id,))
        conn.commit()
        
        affected_rows = cursor.rowcount
        cursor.close()
        conn.close()
        
        if affected_rows > 0:
            return jsonify({"result": "success", "message": f"{user_id} 계정이 다시 활성화되었습니다."})
        return jsonify({"result": "fail", "message": "사용자를 찾을 수 없습니다."})
    except Exception as e:
        return jsonify({"result": "fail", "message": str(e)})

@app.route('/delete_user/<string:user_id>', methods=['POST'])
def delete_user(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        query = "UPDATE users SET status = 'Pending_Delete', deleted_at = %s WHERE userid = %s"
        cursor.execute(query, (now, user_id))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"result": "success", "message": "30일 유예 기간 설정 완료"})
    except Exception as e:
        return jsonify({"result": "fail", "message": str(e)})

@app.route('/history')
def diagnosis_history():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # [해결책] 컬럼명을 몰라도 오류가 나지 않도록 전체(*)를 가져옵니다.
        cursor.execute("SELECT * FROM history ORDER BY created_at DESC")
        logs_data = cursor.fetchall()
        
        # [중요] 터미널(검은창)에서 실제 컬럼 순서를 확인하기 위해 출력합니다.
        column_names = [i[0] for i in cursor.description]
        print(f"★ 현재 history 테이블 컬럼 구성: {column_names}")

        cursor.close()
        conn.close()
        
        return render_template('admin_history.html', logs_list=logs_data)
    except Exception as e:
        # 에러 발생 시 브라우저에 구체적인 내용을 띄웁니다.
        print(f"진단로그 로드 실패: {e}")
        return f"진단 로그 로드 오류: {e}"

@app.route('/experts')
def expert_management():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, business_no, specialty, status FROM experts ORDER BY id DESC")
        expert_list = cursor.fetchall()
        cursor.close()
        conn.close()
        return render_template('admin_experts.html', experts=expert_list)
    except Exception as e:
        return f"업체관리 로드 실패: {e}"

if __name__ == '__main__':
    app.run(debug=True, port=8000)