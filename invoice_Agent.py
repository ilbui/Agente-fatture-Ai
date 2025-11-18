import streamlit as st
import pdfplumber
import re
import pandas as pd
import json
import requests
from datetime import datetime

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(
    page_title="Agente Locale Fatture PDF (Phi-3)",
    page_icon="🧾",
    layout="wide"
)

# --- FUNZIONI DI UTILITÀ ---

def extract_text_from_pdf(uploaded_file):
    """Estrae il testo grezzo dal PDF usando pdfplumber."""
    text = ""
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            for page in pdf.pages:
                text += page.extract_text() + "\n"
    except Exception as e:
        st.error(f"Errore nella lettura del PDF: {e}")
    return text

def analyze_invoice_regex(text):
    """
    Tentativo di estrazione basato su regole (Regex).
    Funziona offline senza bisogno di LLM.
    """
    data = {
        "Fornitore": None,
        "Data": None,
        "Numero Fattura": None,
        "Importo Totale": None
    }

    # 1. Cerca Date (formati comuni: dd/mm/yyyy, yyyy-mm-dd)
    date_pattern = r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b'
    dates = re.findall(date_pattern, text)
    if dates:
        data["Data"] = dates[0] # Prende la prima data trovata

    # 2. Cerca Importi (cerca pattern con simboli valuta o parole chiave)
    amount_pattern = r'(?:Totale|Importo|Total|Balance)[\D]*?(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})'
    amounts = re.findall(amount_pattern, text, re.IGNORECASE)
    if amounts:
        data["Importo Totale"] = amounts[-1] # Spesso il totale è l'ultimo importo
    
    # 3. Cerca Numero Fattura
    invoice_num_pattern = r'(?:Fattura|Invoice|N\.|No\.)\s*[:#]?\s*([A-Za-z0-9\-/]+)'
    inv_nums = re.findall(invoice_num_pattern, text, re.IGNORECASE)
    if inv_nums:
        data["Numero Fattura"] = inv_nums[0]

    # 4. Fornitore (Euristica semplice: prima riga non vuota)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    if lines:
        data["Fornitore"] = lines[0]

    return data

def analyze_invoice_ollama(text, model_name="phi3"):
    """
    Funzione per chiamare un LLM Locale tramite Ollama.
    Default impostato su Phi-3 Mini.
    """
    # Phi-3 ha una context window più piccola di Llama 3 in alcune versioni,
    # ma 3000 caratteri vanno bene. Il prompt è ottimizzato per modelli piccoli.
    prompt = f"""
    Sei un assistente amministrativo. Analizza questa fattura ed estrai i dati in JSON.
    
    Testo Fattura:
    {text[:3000]}

    Campi richiesti:
    - fornitore
    - data_fattura (YYYY-MM-DD)
    - numero_fattura
    - importo_totale (numero decimale, usa il punto)
    - valuta
    - elenco_articoli (lista sintetica)

    Rispondi SOLO con il JSON valido.
    """

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model_name,
                "prompt": prompt,
                "stream": False,
                "format": "json" 
            },
            timeout=160 # Aumentato leggermente timeout per sicurezza
        )
        if response.status_code == 200:
            result = response.json()
            return json.loads(result['response'])
        else:
            return None
    except Exception as e:
        st.warning(f"Impossibile connettersi a Ollama ({e}). Uso fallback Regex.")
        return None

# --- INTERFACCIA UTENTE ---

st.title("🧾 Agente Locale Fatture PDF (con Phi-3)")
st.markdown("""
Questo strumento analizza le fatture usando **Phi-3 Mini** (Microsoft) in locale.
I dati **non** lasciano mai questo dispositivo.
""")

# Sidebar per impostazioni
with st.sidebar:
    st.header("⚙️ Impostazioni")
    use_ai = st.checkbox("Usa AI Locale (Ollama)", value=True, help="Richiede Ollama attivo.")
    
    # Modifica qui: Default value impostato su "phi3"
    model_name = st.text_input("Modello Ollama", value="phi3")
    
    st.info(f"Modello attuale: **{model_name}**")
    if use_ai:
        st.caption("Assicurati di aver lanciato nel terminale: `ollama pull phi3`")

# Upload File
uploaded_files = st.file_uploader("Carica le tue fatture (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    st.divider()
    
    results_list = []

    # Barra di progresso
    progress_bar = st.progress(0)
    
    for i, uploaded_file in enumerate(uploaded_files):
        col1, col2 = st.columns([1, 3])
        
        with col1:
            # Icona PDF generica
            st.markdown("📄", unsafe_allow_html=True)
            st.caption(uploaded_file.name)

        # 1. Estrazione Testo
        text = extract_text_from_pdf(uploaded_file)
        
        # 2. Analisi (AI o Regex)
        extracted_data = {}
        method_used = "Regex"
        
        if use_ai:
            with st.spinner(f"Phi-3 sta leggendo {uploaded_file.name}..."):
                ai_data = analyze_invoice_ollama(text, model_name)
                if ai_data:
                    extracted_data = ai_data
                    method_used = f"AI ({model_name})"
                else:
                    extracted_data = analyze_invoice_regex(text)
                    method_used = "Regex (Fallback)"
        else:
            extracted_data = analyze_invoice_regex(text)

        # Aggiungi metadati file
        extracted_data["Nome File"] = uploaded_file.name
        extracted_data["Metodo"] = method_used
        results_list.append(extracted_data)

        # Visualizzazione Anteprima Rapida
        with col2:
            st.json(extracted_data, expanded=False)
        
        progress_bar.progress((i + 1) / len(uploaded_files))

    st.success("Analisi completata!")

    # --- TABELLA RIEPILOGATIVA ---
    st.subheader("📊 Riepilogo Dati Estratti")
    df = pd.DataFrame(results_list)
    
    # Riordina colonne in modo intelligente se esistono
    preferred_order = ["Nome File", "data_fattura", "Data", "fornitore", "Fornitore", "importo_totale", "Importo Totale"]
    existing_cols = [c for c in preferred_order if c in df.columns]
    remaining_cols = [c for c in df.columns if c not in preferred_order]
    df = df[existing_cols + remaining_cols]

    st.dataframe(df, use_container_width=True)

    # --- EXPORT ---
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Scarica Report Excel/CSV",
        data=csv,
        file_name=f"export_fatture_{datetime.now().strftime('%Y%m%d')}.csv",
        mime='text/csv',
    )

else:
    st.info("Carica un PDF per iniziare.")
    st.markdown("### Requisiti:")
    st.markdown("1. Installa Ollama da [ollama.com](https://ollama.com)")
    st.markdown("2. Esegui nel terminale: `ollama pull phi3`")
    st.markdown("3. Installa le librerie python: `pip install streamlit pdfplumber pandas requests`")