import streamlit as st
import pdfplumber
import pandas as pd
import io
import os
import re
from tempfile import NamedTemporaryFile
from datetime import datetime

st.set_page_config(page_title="CNC Router Cycle Time", layout="wide")
st.title("CNC Router Cycle Time")
st.markdown("📌 For any issues related to the app, please contact Mark Dang.")

standard_columns = [
    "Part ID",
    "Part Name",
    "Cart Loading",
    "Qty Req",
    "Qty Nested",
    "Part Description",
    "Production Instructions",
    "Material"
]

def clean_and_align_table(df_raw):
    df_raw = df_raw.dropna(how="all").reset_index(drop=True)

    def is_col_empty_or_zero(col):
        all_none = col.isna().all()
        try:
            all_zero = (col.fillna(0).astype(float) == 0).all()
        except:
            all_zero = False
        return all_none or all_zero

    df_raw = df_raw[[col for col in df_raw.columns if not is_col_empty_or_zero(df_raw[col])]]
    n_col = df_raw.shape[1]

    if n_col == 8:
        df_raw.columns = standard_columns
    elif n_col == 7:
        temp_cols = [col for col in standard_columns if col != "Cart Loading"]
        df_raw.columns = temp_cols
        df_raw.insert(2, "Cart Loading", pd.NA)
    else:
        raise ValueError(f"❌ Bảng có {n_col} cột. Yêu cầu 7 hoặc 8 cột.")

    return df_raw

def extract_data_from_pdf(file_bytes, filename):
    all_tables = []
    base_name = os.path.splitext(filename)[0]
    page_count = 0

    with NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_bytes.getvalue())
        tmp_path = tmp.name

    with pdfplumber.open(tmp_path) as pdf:
        page_count = len(pdf.pages)  # Đếm số trang PDF
        
        full_text = "\n".join([page.extract_text() or "" for page in pdf.pages])
        match = re.search(r"(\d+(\.\d+)?)\s*Sheet\(s\)\s*=\s*(\d+(\.\d+)?)\s*Kit\(s\)", full_text, re.IGNORECASE)
        sheet_count = float(match.group(1)) if match else None
        kit_count = float(match.group(3)) if match else None

        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                continue
            for table in tables:
                if not table or len(table) < 2:
                    continue

                data_rows = table[1:]
                df_temp = pd.DataFrame(data_rows)
                df_temp = df_temp[~df_temp.apply(lambda row: row.astype(str).str.contains("Yield:", case=False).any(), axis=1)]

                if df_temp.empty:
                    continue

                try:
                    df_clean = clean_and_align_table(df_temp)
                    df_clean.insert(1, "Program", base_name)
                    df_clean["Sheet"] = sheet_count
                    df_clean["Kit"] = kit_count
                    df_clean["PageCount"] = page_count  # Thêm số trang
                    all_tables.append(df_clean)
                except Exception as e:
                    st.warning(f"⚠️ Lỗi khi xử lý bảng từ {filename}: {e}")

    return pd.concat(all_tables, ignore_index=True) if all_tables else pd.DataFrame()

def calculate_part_num(description):
    """
    Tính Part Num dựa trên Part Description:
    - Chứa "RELIEF" → 0
    - Có pattern L[text][SPECIAL_CHAR][text]R[text] → 2
      (SPECIAL_CHAR: /, -, +, &, |, *, #, @, etc. KHÔNG BAO GỒM SPACE và chữ/số)
    - Còn lại → 1
    
    Ví dụ pattern L/R hợp lệ:
    - "L Side / R Side" ✓ (có separator "/")
    - "L-Panel + R-Panel" ✓ (có separator "+")
    - "L End & R End" ✓ (có separator "&")
    - "Leg Rail" ✗ (không có special char separator)
    - "L Side R Side" ✗ (chỉ có space, không có special char)
    """
    if pd.isna(description):
        return 1
    
    desc_str = str(description).strip()
    
    # Kiểm tra RELIEF
    if re.search(r'RELIEF', desc_str, re.IGNORECASE):
        return 0
    
    # Pattern: L + chữ/số + KÝ TỰ ĐẶC BIỆT (không phải space, chữ, số) + bất kỳ gì + R + chữ/số
    # [^\w\s] = không phải word character (chữ/số/_) và không phải space
    # Nghĩa là chỉ ký tự đặc biệt như /, -, +, &, |, *, #, @, etc.
    pattern = r'L[a-zA-Z0-9]+[^\w\s]+.*?R[a-zA-Z0-9]+'
    
    if re.search(pattern, desc_str, re.IGNORECASE):
        return 2
    
    # Còn lại
    return 1

uploaded_files = st.file_uploader("📂 Kéo và thả file PDF vào đây", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    df_list = []
    total = len(uploaded_files)
    progress = st.progress(0)
    status = st.empty()

    for idx, file in enumerate(uploaded_files, 1):
        status.text(f"🔍 Đang xử lý: {file.name} ({idx}/{total})")
        df = extract_data_from_pdf(file, file.name)
        if not df.empty:
            df_list.append(df)
        progress.progress(idx / total)

    if df_list:
        combined_df = pd.concat(df_list, ignore_index=True)
        combined_df = combined_df[combined_df["Part Name"].notna()]

        for col in ["Qty Req", "Qty Nested", "Sheet", "Kit"]:
            combined_df[col] = pd.to_numeric(combined_df[col], errors="coerce").fillna(0)

        # Tạo cột Part Num dựa trên Part Description
        combined_df["Part Num"] = combined_df["Part Description"].apply(calculate_part_num)

        # Tạo bảng kết quả tổng hợp
        result_data = []
        
        for program in combined_df["Program"].unique():
            program_df = combined_df[combined_df["Program"] == program]
            
            # SUM cột Part Num để tính Different Parts
            # Logic:
            # - RELIEF parts: Part Num = 0
            # - L/R pattern parts (với ký tự đặc biệt, không tính space): Part Num = 2
            # - Regular parts: Part Num = 1
            different_parts = program_df["Part Num"].sum()
            
            # Tổng số parts (Qty Nested)
            total_parts = program_df["Qty Nested"].sum()
            
            # Lấy giá trị Kit và PageCount
            frames_kit = program_df["Kit"].iloc[0] if not program_df.empty else None
            number_of_tables = program_df["PageCount"].iloc[0] if not program_df.empty else None
            
            # Ngày hiện tại
            today = datetime.now().strftime("%m/%d/%Y")
            
            result_data.append({
                "Status": "",
                "Program": program,
                "Cycle Time": "",
                "Different Parts": int(different_parts),
                "Total # of parts": int(total_parts),
                "Frames/kit": frames_kit,
                "Number of Tables": int(number_of_tables) if number_of_tables else None,
                "Date cycle time was done": today
            })
        
        result_df = pd.DataFrame(result_data)
        
        st.success("✅ Hoàn tất xử lý!")
        
        # Hiển thị thông tin về logic đếm
        st.info(
            "ℹ️ **Counting rules:**\n"
            "- RELIEF parts = 0\n"
            "- L/R pattern parts (with special char separator, NOT space) = 2\n"
            "- Regular parts = 1"
        )
        
        st.dataframe(result_df, use_container_width=True)

        # Export file Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            result_df.to_excel(writer, index=False, sheet_name="Summary")
        st.download_button(
            label="📥 Tải Excel kết quả",
            data=output.getvalue(),
            file_name="extracted_summary.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.error("❌ Không tìm thấy dữ liệu hợp lệ.")
