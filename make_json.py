import json
import os
import xml.etree.ElementTree as ET
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

def make_homefix_json():
    final_data = []
    folder = "homefix/"
    
    pdf_files = [
        (folder + "★공동주택 보수공사 길라잡이(책자발간본)★.pdf", "보수공사 길라잡이"),
        (folder + "경기도 공동주택관리매뉴얼 현황.pdf", "공동주택관리 매뉴얼")
    ]
    xml_filename = folder + "한국소비자원_품목별 피해구제 사례_20220331.xml"

    # ✅ 청크를 더 작게(500자), 겹침은 더 많이(100자) 설정하여 개수 확보
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100,
        length_function=len,
    )

    # --- [섹션 1] PDF 처리 ---
    for filename, source_name in pdf_files:
        if os.path.exists(filename):
            print(f"📖 PDF 처리 중: {filename}")
            try:
                loader = PyPDFLoader(filename)
                pages = loader.load()
                
                # 텍스트가 거의 없는 페이지는 스캔본일 수 있음
                split_docs = text_splitter.split_documents(pages)
                
                for doc in split_docs:
                    content = doc.page_content.strip()
                    if len(content) > 20: # 최소 글자수 완화
                        final_data.append({
                            "content": content,
                            "metadata": {
                                "source": source_name,
                                "type": "pdf"
                            }
                        })
                print(f"   -> 현재 누적 조각 수: {len(final_data)}개")
            except Exception as e:
                print(f"❌ PDF 에러: {e}")

    # --- [섹션 2] XML 처리 (와일드카드 방식) ---
    if os.path.exists(xml_filename):
        print(f"📂 XML 강제 추출 중: {xml_filename}")
        try:
            # 소비자원 XML 인코딩 이슈 대응을 위해 바이트로 읽기
            with open(xml_filename, 'rb') as f:
                xml_data = f.read()
            root = ET.fromstring(xml_data)
            
            # 태그 이름을 몰라도 모든 하위 노드를 뒤져서 텍스트를 가져옵니다.
            # 보통 <사례명>, <내용>, <결과> 등이 들어있는 부모 태그를 찾습니다.
            xml_count = 0
            # 공공데이터 XML의 흔한 구조인 'record', 'list', 'item', 'row' 모두 뒤지기
            for entry in root.iter():
                # 데이터가 담긴 노드인지 판단 (자식 노드가 2개 이상 있는 경우를 데이터 행으로 간주)
                if len(entry) >= 2:
                    # 해당 노드 안의 모든 텍스트를 합칩니다.
                    text_parts = [child.text.strip() for child in entry if child.text]
                    if text_parts:
                        combined_text = "[소비자원 사례] " + " | ".join(text_parts)
                        if len(combined_text) > 50:
                            final_data.append({
                                "content": combined_text,
                                "metadata": {"source": "한국소비자원", "type": "xml"}
                            })
                            xml_count += 1
            
            print(f"✅ XML 데이터 {xml_count}건 강제 추출 성공!")
            
        except Exception as e:
            print(f"❌ XML 에러: {e}")

    # --- [섹션 3] JSON 저장 ---
    output_file = 'homefix_data.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n✨ 최종 완료! 총 {len(final_data)}개의 지식 조각이 저장되었습니다.")

if __name__ == "__main__":
    make_homefix_json()