import streamlit as st
import pdfplumber
import re
import pandas as pd
from datetime import datetime
from io import BytesIO

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(
    page_title="Estrattore Dati Fatture Attive",
    page_icon="📬",
    layout="wide"
)

# -------------------------------------------------------------------
# FUNZIONI DI UTILITÀ
# -------------------------------------------------------------------

def clean_number_str(val: str) -> float:
    if not val: return 0.0
    s = str(val).replace("€", "").strip()
    if "," in s: s = s.replace(".", "").replace(",", ".")
    try: return float(s)
    except ValueError: return 0.0

def format_ita_currency(num: float) -> str:
    return f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def extract_text_from_pdf(uploaded_file) -> str:
    text = ""
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text(layout=True, x_tolerance=2, y_tolerance=2) or ""
                text += page_text + "\n"
    except Exception as e:
        st.error(f"Errore lettura PDF: {e}")
    return text

def get_amounts_in_line(line: str) -> list[float]:
    matches = re.findall(r"\b\d{1,3}(?:[.,]\d{3})*[.,]\d{2}\b", line)
    values = []
    for m in matches:
        val = clean_number_str(m)
        if val > 0.01: values.append(val)
    return values

def is_date(string: str) -> bool:
    return bool(re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", string))

def is_address_line(line: str) -> bool:
    address_keywords = ["via ", "viale ", "piazza ", "corso ", "strada ", "vicolo ", "contrada "]
    line_lower = line.lower()
    return any(kw in line_lower for kw in address_keywords)

# -------------------------------------------------------------------
# LOGICA DI ESTRAZIONE
# -------------------------------------------------------------------

def parse_invoice_smart(text: str) -> dict:
    data = {
        "Data": None,
        "Numero": None,
        "Destinatario": None, # Solo Nome Azienda
        "Indirizzo": None,    # Via, Città, CAP
        "Importo": "0,00",
        "Spese Generali": "0,00",
        "Totale": "0,00"
    }

    if not text: return data

    lines = [line.strip() for line in text.split("\n") if line.strip()]

    # --- 1. DATA ---
    date_match = re.search(r"(?:Data|del|Li)\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})", text, re.IGNORECASE)
    if date_match:
        data["Data"] = date_match.group(1)
    else:
        dates = re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", text)
        if dates: data["Data"] = dates[0]

    # --- 2. NUMERO ---
    found_numero = None
    forbidden_words = ["pagina", "page", "data", "date", "fattura", "invoice", "telefono", "tel", "fax", "cap", "iva", "codice", "fiscale"]

    candidates = []
    for line in lines:
        if is_address_line(line): continue
        tokens = re.findall(r"\b[A-Z0-9][A-Z0-9\-/]{0,15}\b", line, re.IGNORECASE)
        for t in tokens: candidates.append(t)

    for cand in candidates:
        cand_clean = cand.lower().strip()
        if not any(char.isdigit() for char in cand): continue
        if is_date(cand): continue
        if cand.isdigit() and len(cand) == 4 and int(cand) > 2000: continue
        if cand_clean in forbidden_words: continue

        if "/" in cand or "-" in cand or (any(c.isalpha() for c in cand) and any(c.isdigit() for c in cand)):
            found_numero = cand
            break
        if found_numero is None: found_numero = cand

    data["Numero"] = found_numero

    # --- 3. DESTINATARIO E INDIRIZZO (SEPARATI) ---
    dest_start = re.compile(r"(?:Spett\.le|Spett/le|Cliente|Destinatario)\s*[:.]?", re.IGNORECASE)
    
    raw_lines = []
    
    # Raccogliamo tutto il blocco destinatario in una lista
    for i, line in enumerate(lines):
        if dest_start.search(line):
            cleaned = dest_start.sub("", line).strip()
            if len(cleaned) > 2: raw_lines.append(cleaned)
            
            # Prendi le successive 3-4 righe
            for j in range(1, 5):
                if i + j < len(lines):
                    nxt = lines[i+j]
                    if re.search(r"(P\.?IVA|Codice|Data|Fattura)", nxt, re.IGNORECASE): break
                    raw_lines.append(nxt)
            break
    
    # Ora separiamo Nome da Indirizzo
    if raw_lines:
        data["Destinatario"] = raw_lines[0] # La prima riga è il Nome (es. TFB S.R.L.)
        
        if len(raw_lines) > 1:
            # Uniamo tutte le altre righe per formare l'indirizzo completo
            data["Indirizzo"] = " ".join(raw_lines[1:])

    # --- 4. VALORI ---
    regex_importo = [r"Compensi\s*dovuti", r"Onorari", r"Attività\s*di\s*assistenza"]
    regex_spese   = [r"Spese\s*generali", r"15\s*%", r"ex\s*D\.M\."]
    regex_totale  = [r"Totale\s*onorari"] 

    def find_value(lines, regex_list, strategy='first'):
        for idx, line in enumerate(lines):
            for pattern in regex_list:
                if re.search(pattern, line, re.IGNORECASE):
                    vals = get_amounts_in_line(line)
                    if not vals and idx + 1 < len(lines):
                        vals = get_amounts_in_line(lines[idx+1])
                    if vals:
                        if strategy == 'first': return vals[0]
                        elif strategy == 'max': return max(vals)
                        elif strategy == 'last': return vals[-1]
        return 0.0

    val_importo = find_value(lines, regex_importo, strategy='max')
    val_spese = find_value(lines, regex_spese, strategy='max')
    val_totale = find_value(lines, regex_totale, strategy='first')

    if val_importo > 0: data["Importo"] = format_ita_currency(val_importo)
    if val_spese > 0: data["Spese Generali"] = format_ita_currency(val_spese)
    if val_totale > 0: data["Totale"] = format_ita_currency(val_totale)
    
    return data

# -------------------------------------------------------------------
# INTERFACCIA
# -------------------------------------------------------------------

st.title("📬 Estrattore Dati Fatture Attive")

uploaded_files = st.file_uploader("Carica PDF", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    st.divider()
    results = []
    for f in uploaded_files:
        txt = extract_text_from_pdf(f)
        d = parse_invoice_smart(txt)
        row = {"Nome File": f.name}
        row.update(d)
        results.append(row)
        
    df = pd.DataFrame(results)
    # NUOVO ORDINE COLONNE
    cols = ["Nome File", "Data", "Numero", "Destinatario", "Indirizzo", "Importo", "Spese Generali", "Totale"]
    final_cols = [c for c in cols if c in df.columns]
    
    st.dataframe(df[final_cols], use_container_width=True)
    
    try:
        import xlsxwriter
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
            df[final_cols].to_excel(writer, index=False, sheet_name='Dati')
            # Allarga un po' le colonne Destinatario e Indirizzo
            writer.sheets['Dati'].set_column(3, 4, 30) 
            writer.sheets['Dati'].set_column(0, 2, 15)
        st.download_button("📥 Scarica Excel", buf.getvalue(), f"Export_{datetime.now().strftime('%H%M')}.xlsx", "application/vnd.ms-excel")
    except:
        st.download_button("📥 Scarica CSV", df.to_csv(sep=";", index=False).encode("utf-8-sig"), "Export.csv", "text/csv")