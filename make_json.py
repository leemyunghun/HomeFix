import json
import os
import pandas as pd
import unicodedata
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

def normalize_caseless(text):
    """한글 자모음 분리 및 인코딩 통합 (NFC 정규화)"""
    if not text: return ""
    text = unicodedata.normalize('NFC', text)
    return text.replace(" ", "").replace("+", "").replace("_", "").lower()

def find_file_smart(target_name, folder_files):
    """폴더 내 파일 목록에서 유사한 이름을 찾아 반환"""
    norm_target = normalize_caseless(target_name)
    target_base = os.path.splitext(norm_target)[0]
    
    for f in folder_files:
        norm_f = normalize_caseless(f)
        if norm_target == norm_f or target_base in norm_f:
            return f
    return None

def make_homefix_json():
    final_data = []
    data_folder = "homefix" 
    
    if not os.path.exists(data_folder):
        print(f"❌ '{data_folder}' 폴더를 찾을 수 없습니다.")
        return

    all_files = os.listdir(data_folder)
    print(f"✅ '{data_folder}' 폴더 내 파일 {len(all_files)}개 탐지됨.")

    # 1. PDF 대상 목록
    pdf_targets = [
        ("집수리 팁(3) 집수리 지원.pdf", "집수리 행정 지원 가이드"),
        ("(최종본)+세면대+안전사고+주의보_보도자료.pdf", "소비자원 세면대 안전주의보"),
        ("250325_가정+내+안전사고+관련+소비자안전주의보_보도자료.pdf", "가정 내 안전사고 주의보"),
        ("붙임_「2023 공동주택관리 매뉴얼」 파일(PDF).pdf", "2023 공동주택관리 매뉴얼"),
        ("알기쉬운 집수리 길라잡이.pdf", "집수리 길라잡이"),
        ("자가용전기설비검사업무적용 핸드북 (2022년).PDF", "전기설비 검사 핸드북"),
        ("제17회 공동주택관리 열린강좌 교재(공동주택 장기수선계획 및 시설물 안전관리).pdf", "시설물 안전관리 교재"),
        ("집수리 매뉴얼.pdf", "서울시 집수리 매뉴얼")
    ]
    
    # 2. CSV 대상 목록
    csv_targets = [
        ("집수리 성공기.csv", "서울시 집수리 성공기"),
        ("집수리 성공기 첨부내용.csv", "성공기 상세 내용"),
        ("서울시 집수리닷컴 공지사항.csv", "서울시 집수리 공지"),
        ("서울시 집수리 시공업체 정보.csv", "서울시 시공업체 정보")
    ]

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=120)

    # --- PDF 처리 섹션 ---
    print("\n--- PDF 파일 처리 시작 ---")
    for target_name, source_name in pdf_targets:
        actual_name = find_file_smart(target_name, all_files)
        if actual_name:
            file_path = os.path.join(data_folder, actual_name)
            try:
                loader = PyPDFLoader(file_path)
                pages = loader.load()
                split_docs = text_splitter.split_documents(pages)
                for doc in split_docs:
                    final_data.append({
                        "content": doc.page_content.strip(),
                        "metadata": {"source": source_name, "type": "pdf", "file": actual_name}
                    })
                print(f"📖 PDF 매칭 성공: [{actual_name}]")
            except Exception as e:
                print(f"   ❌ PDF 처리 에러 ({actual_name}): {e}")

    # --- CSV 처리 섹션 ---
    print("\n--- CSV 파일 처리 시작 ---")
    for target_name, source_name in csv_targets:
        actual_name = find_file_smart(target_name, all_files)
        if actual_name:
            file_path = os.path.join(data_folder, actual_name)
            
            df = None
            # 인코딩 오류 방지를 위한 다중 시도
            for enc in ['utf-8-sig', 'cp949', 'euc-kr', 'utf-8']:
                try:
                    df = pd.read_csv(file_path, encoding=enc)
                    break
                except:
                    continue
            
            if df is not None:
                print(f"📊 CSV 매칭 성공: [{actual_name}]")
                count = 0
                for _, row in df.iterrows():
                    # ✅ [중요] 시공업체 정보일 경우 검색용 메타데이터 별도 구성
                    if "시공업체" in source_name:
                        # 업체명이나 주소가 빈 값인 경우 건너뜀
                        if pd.isna(row.get('업체명')) or pd.isna(row.get('업체주소')):
                            continue
                            
                        final_data.append({
                            "content": f"업체명: {row.get('업체명')} | 주요분야: {row.get('주요시공분야')}",
                            "metadata": {
                                "source": source_name,
                                "type": "csv",
                                "category": "expert",  # ✅ 검색 필터용 키
                                "name": str(row.get('업체명', '')),
                                "location": str(row.get('업체주소', '')),
                                "contact": str(row.get('업체연락처', '정보없음'))
                            }
                        })
                    else:
                        # 일반 수리 성공기 등 텍스트 데이터 처리
                        row_text = " | ".join([f"{col}: {val}" for col, val in row.items() if pd.notna(val)])
                        final_data.append({
                            "content": f"[{source_name}] {row_text}",
                            "metadata": {"source": source_name, "type": "csv", "file": actual_name}
                        })
                    count += 1
                print(f"   ✅ {count}건 처리 완료")
            else:
                print(f"   ❌ CSV 로드 실패 (인코딩 확인 필요): {actual_name}")

    # --- JSON 파일 저장 ---
    with open('homefix_data_final.json', 'w', encoding='utf-8') as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n🎉 모든 작업 완료! 총 {len(final_data)}개의 조각이 'homefix_data_final.json'에 저장되었습니다.")

if __name__ == "__main__":
    make_homefix_json()