import streamlit as st
import fitz  # PyMuPDF
import json
import pandas as pd
from io import BytesIO
import re
import os
from collections import Counter
from pathlib import Path

def load_configs(config_path='configs.json'):
    with open(config_path, 'r') as f:
        return json.load(f)

CONFIGS = load_configs()


def pdf_to_lines(pdf_file):
    doc = fitz.open(stream=pdf_file.read(), filetype="pdf")
    full_text = ""
    for page in doc:
        full_text += page.get_text("text")
    lines = [line.strip() for line in full_text.split('\n') if line.strip()]
    return {f"line_{i+1}": line for i, line in enumerate(lines)}

def split_content_into_sections(line_data, configs):
    cfg = configs['SECTIONING']
    sections, buffer, section_id = {}, {}, 0
    sorted_keys = sorted(line_data.keys(), key=lambda x: int(x.split('_')[1]))

    for k in sorted_keys:
        line_text = line_data[k]
        if cfg['START_MARKER'] in line_text:
            if section_id > 0:
                sections[f"{cfg['KEY_PREFIX']}_{section_id}"] = buffer
            match = re.search(cfg['ID_PATTERN'], line_text)
            if match:
                section_id = int(match.group(1))
                buffer = {k: line_text}
        else:
            if section_id > 0: buffer[k] = line_text
    if section_id > 0: sections[f"{cfg['KEY_PREFIX']}_{section_id}"] = buffer
    return sections
# --- CORE LOGIC (Kept from info-collector.py) ---

def get_standardized_location(raw_input, configs):
    """Standardizes location names using a mapping and fallback rules from configuration."""
    clean_text = raw_input.upper()
    
    # Load the mapping from the configuration file
    location_mapping = configs['LOCATIONS']['ALIAS_MAP']
    
    # Iterate through keyword-to-name mappings
    for keyword, official_name in location_mapping.items():
        if keyword in clean_text:
            return official_name
            
    # Fallback logic for secondary identifiers (e.g., shorthand markers)
    fallback = configs['LOCATIONS']['FALLBACK_RULES']
    if fallback['PRIMARY_MARKER'] in clean_text:
        for sub_marker, result in fallback['SUB_MARKERS'].items():
            if sub_marker in clean_text:
                return result
                
    return raw_input

def parse_event_metadata(header_line, configs):
    """Extracts location and timing information from a standardized section header."""
    # Retrieve delimiters from config for complete abstraction
    delimiters = configs['PARSING']
    
    # Split the header into primary segments (e.g., Location and Date)
    segments = header_line.split(delimiters['HEADER_SEGMENT_SEPARATOR'])
    location = get_standardized_location(segments[0].strip(), configs)
    
    # Process the date segment
    date_part = segments[1]
    month_day, year = date_part.split(delimiters['DATE_YEAR_SEPARATOR'])
    month, day = month_day.strip().split(delimiters['MONTH_DAY_SEPARATOR'])
    
    return {
        "Location": location,
        "Date": {
            "Month": month.lower(),
            "Day": int(day),
            "Year": int(year)
        }
    }

def get_event_description(section_lines, configs):
    """
    Extracts descriptive text from a section based on configured 
    start indices and terminal anchors.
    """
    description_buffer = []
    # Sort keys numerically (line_1, line_2...)
    line_keys = sorted(section_lines.keys(), key=lambda x: int(x.split('_')[1]))
    
    # Load function-specific configuration
    cfg = configs['EVENT_DESCRIPTION']
    
    for i, k in enumerate(line_keys):
        # Skip initial metadata lines based on the configured offset
        if i >= cfg['START_INDEX']:
            text = section_lines[k].strip()
            
            # Check for the stop anchor (e.g., "DISTANCE") to end extraction
            if text.upper().startswith(cfg['STOP_ANCHOR'].upper()):
                break
                
            description_buffer.append(text)
            
    return " ".join(description_buffer)

def get_start_time(section_lines, configs):
    """
    Scans the section lines for a specific start-time marker 
    and extracts the timestamp using a configured pattern.
    """
    # Load function-specific configuration
    cfg = configs['EVENT_START_TIME']
    
    for text in section_lines.values():
        if cfg['SEARCH_TERM'] in text:
            # Extract the time (e.g., HH:MM) using the configured regex pattern
            match = re.search(cfg['TIME_PATTERN'], text)
            if match:
                return match.group(1)
                
    return None

def enhance_event_times(events_data, configs):
    """
    Calculates and adds normalized 24-hour time values to event metadata 
    based on the sequence of entries.
    """
    # Load required configurations
    section_cfg = configs['SECTIONING']
    time_cfg = configs['TIME_ENHANCEMENT']
    
    prefix = section_cfg['KEY_PREFIX']
    
    sorted_keys = sorted(
        [k for k in events_data.keys() if f"{prefix}_" in k], 
        key=lambda x: int(x.split('_')[1])
    )
    
    is_afternoon = False
    for key in sorted_keys:
        info = events_data[key]
        
        # Check if the event was marked with a specific status (e.g., "Cancelled")
        if info == time_cfg['STATUS_SKIP_MARKER']:
            continue
            
        # Retrieve the raw time string using the configured metadata key
        raw_time = info.get("Date", {}).get(time_cfg['SOURCE_KEY'])
        
        if raw_time:
            hour, minute = map(int, raw_time.split(':'))
            
            # Determine if the timeline has rolled over to PM
            if hour == 12 or hour < time_cfg['PM_THRESHOLD']:
                is_afternoon = True
            
            # Apply 24-hour conversion if necessary
            normalized_hour = hour + 12 if (is_afternoon and hour < 12) else hour
            
            # Save the formatted result to the configured output key
            output_key = time_cfg['OUTPUT_KEY']
            info["Date"][output_key] = f"{normalized_hour:02d}:{minute:02d}"

def get_event_metric(section_lines, configs):
    """
    Scans section lines for a specific metric label and extracts the 
    associated value by splitting the string at a configured anchor point.
    """
    # Load function-specific configuration
    cfg = configs['EVENT_METRIC']
    search_term = cfg['SEARCH_TERM']
    anchor_term = cfg['ANCHOR_TERM']
    
    for text in section_lines.values():
        # Check if the line begins with the target label (case-insensitive)
        if text.strip().upper().startswith(search_term.upper()):
            # 1. Isolate the segment before the anchor term
            # 2. Extract the value following the search term
            try:
                segment = text.split(anchor_term)[0]
                value = segment.split(search_term)[1].strip()
                return value
            except (IndexError, ValueError):
                continue
                
    return None

def get_primary_value(section_lines, configs):
    """
    Scans section lines for a primary financial indicator and 
    returns the cleaned integer value.
    """
    # Load function-specific configuration
    cfg = configs['PRIMARY_VALUE']
    search_term = cfg['SEARCH_TERM']
    
    for text in section_lines.values():
        if search_term in text:
            # 1. Isolate the part after the search term
            raw_value = text.split(search_term)[1]
            
            # 2. Remove configured characters (e.g., symbols and commas)
            for char in cfg['CLEANUP_CHARS']:
                raw_value = raw_value.replace(char, "")
            
            # 3. Extract the first continuous numeric string and convert to int
            try:
                numeric_part = raw_value.strip().split()[0]
                return int(numeric_part)
            except (ValueError, IndexError):
                continue
                
    return None

def get_secondary_value(section_lines, configs):
    """
    Scans section lines for a secondary financial indicator and 
    returns the cleaned integer value.
    """
    # Load function-specific configuration
    cfg = configs['SECONDARY_VALUE']
    search_term = cfg['SEARCH_TERM']
    
    for text in section_lines.values():
        if search_term in text:
            # 1. Isolate the part after the search term
            raw_value = text.split(search_term)[1]
            
            # 2. Remove configured characters (e.g., symbols and commas)
            for char in cfg['CLEANUP_CHARS']:
                raw_value = raw_value.replace(char, "")
            
            # 3. Extract the first continuous numeric string and convert to int
            try:
                numeric_part = raw_value.strip().split()[0]
                return int(numeric_part)
            except (ValueError, IndexError):
                continue
                
    return None

def get_environmental_status(section_lines, configs):
    """
    Extracts environmental and surface conditions from section lines 
    using configured search markers and value patterns.
    """
    # Load function-specific configuration
    cfg = configs['ENVIRONMENTAL_DATA']
    
    for text in section_lines.values():
        # Check for the line containing both configured markers
        if cfg['MARKER_PRIMARY'] in text and cfg['MARKER_SECONDARY'] in text:
            # 1. Extract the primary condition (e.g., atmospheric state)
            primary = text.split(cfg['MARKER_PRIMARY'])[1].split(cfg['DELIMITER'])[0].strip()
            
            # 2. Extract the secondary status (e.g., surface condition)
            secondary = text.split(cfg['MARKER_SECONDARY'])[1].strip()
            
            # 3. Extract the numerical value (e.g., temperature)
            val_match = re.search(cfg['VALUE_PATTERN'], text)
            value = int(val_match.group(1)) if val_match else None
            
            return primary, value, secondary
            
    return None, None, None

def get_participant_data(section_lines, configs):
    """
    Extracts structured participant information from a section using a 
    repeating line pattern and configured offsets.
    """
    participants = []
    # Sort keys numerically (line_1, line_2...)
    line_keys = sorted(section_lines.keys(), key=lambda x: int(x.split('_')[1]))

    # Load function-specific configuration
    cfg = configs['PARTICIPANT_DATA']

    # Find the starting anchor point (e.g., "Odds Comments")
    idx = next((i for i, k in enumerate(line_keys) if cfg['ANCHOR_TERM'] in section_lines[k]), -1)
    if idx == -1:
        return []

    current_rank = 1
    # Iterate through the section using a configured line stride
    for i in range(idx + cfg['START_OFFSET'], len(line_keys), cfg['LINE_STRIDE']):
        if i >= len(line_keys):
            break
            
        main_line = section_lines[line_keys[i]].strip()
        
        # Extract metadata within parentheses (e.g., Origin and Lead)
        parts = re.findall(cfg['SUB_DATA_PATTERN'], main_line)
        if not parts:
            break
            
        # Logic for primary and secondary lead data
        origin, lead = (parts[0], parts[1]) if len(parts) >= 2 else (cfg['DEFAULT_ORIGIN'], parts[0])
        
        # Strip all parenthetical content to isolate the name
        name = re.sub(cfg['CLEANUP_PATTERN'], '', main_line).strip()
        
        # Extract numerical metric at the configured relative offset
        metric_idx = i + cfg['METRIC_OFFSET']
        metric_val = None
        try:
            raw_metric = section_lines[line_keys[metric_idx]].strip()
            # Clean special characters (e.g., favorite markers)
            for char in cfg['METRIC_CLEANUP']:
                raw_metric = raw_metric.replace(char, '')
            metric_val = float(raw_metric)
        except (ValueError, IndexError):
            metric_val = None

        # Build the entry using generic keys from the config
        fields = cfg['FIELDS']
        participants.append({
            fields['NAME']: name,
            fields['RANK']: current_rank,
            fields['ORIGIN']: origin,
            fields['LEAD']: lead,
            fields['METRIC']: metric_val
        })
        current_rank += 1
        
    return participants

def get_excluded_entities(section_lines, configs):
    """
    Identifies and parses entities that were removed or excluded from a section
    based on configured search terms and reason patterns.
    """
    excluded_list = []
    cfg = configs['EXCLUSION_DATA']
    
    for text in section_lines.values():
        if cfg['SEARCH_TERM'] in text:
            # 1. Isolate the list of excluded entities
            raw_content = text.split(cfg['SEARCH_TERM'])[1].strip()
            
            # 2. Split by delimiter while ignoring delimiters inside parentheses
            # Pattern is now pulled from configuration
            parts = re.split(cfg['SPLIT_PATTERN'], raw_content)
            
            for part in parts:
                clean_part = part.strip()
                if not clean_part:
                    continue
                
                # 3. Extract the reason/cause code from parentheses
                reason_match = re.search(cfg['REASON_PATTERN'], clean_part)
                reason = reason_match.group(1) if reason_match else cfg['DEFAULT_REASON']
                
                # 4. Strip parenthetical data to isolate the entity name
                name = re.sub(cfg['CLEANUP_PATTERN'], '', clean_part).strip()
                
                # 5. Build entry using generic keys from config
                fields = cfg['FIELDS']
                excluded_list.append({
                    fields['NAME']: name,
                    fields['STATUS']: cfg['STATUS_LABEL'],
                    fields['CAUSE']: reason
                })
                
    return excluded_list

def get_aggregate_metric(section_lines, configs):
    """
    Scans section lines for an aggregate summary value and 
    returns the cleaned integer result.
    """
    # Load function-specific configuration
    cfg = configs['AGGREGATE_METRIC']
    search_term = cfg['SEARCH_TERM']
    
    for text in section_lines.values():
        if search_term in text:
            # 1. Isolate the data following the marker
            raw_value = text.split(search_term)[1]
            
            # 2. Strip prohibited characters (e.g., symbols and separators)
            for char in cfg['CLEANUP_CHARS']:
                raw_value = raw_value.replace(char, "")
            
            # 3. Extract the first continuous numeric block and convert to int
            try:
                numeric_part = raw_value.strip().split()[0]
                return int(numeric_part)
            except (ValueError, IndexError):
                continue
                
    return None

# --- MAIN UNIFIED PROCESSOR ---

def process_document_batch(input_directory, output_directory, configs):
    """
    Coordinates the extraction and transformation of data across multiple 
    documents, ensuring cross-section consistency through multi-pass analysis.
    """
    # Ensure the output directory exists
    Path(output_directory).mkdir(parents=True, exist_ok=True)
    
    # Load batch-specific configuration
    cfg = configs['BATCH_PROCESSING']
    m_keys = cfg['METADATA_KEYS']

    for filename in os.listdir(input_directory):
        if filename.endswith(".pdf"):
            st.write(f"Processing: {filename}") # Assuming Streamlit context
            
            # 1. Raw Extraction and Segmentation
            file_path = os.path.join(input_directory, filename)
            raw_lines = pdf_to_lines(file_path) 
            data_sections = split_content_into_sections(raw_lines, configs)
            
            # 2. Pass 1: Site/Location Consensus
            # Collect identifiers across all sections to find the dominant location
            site_identifiers = []
            for section_content in data_sections.values():
                first_k = next(iter(section_content.keys()))
                header_text = section_content.get(first_k, "")
                
                if cfg['HEADER_DELIMITER'] in header_text:
                    raw_id = header_text.split(cfg['HEADER_DELIMITER'])[0]
                    site_identifiers.append(get_standardized_location(raw_id, configs))
            
            primary_site_id = Counter(site_identifiers).most_common(1)[0][0] if site_identifiers else "Unknown"

            # 3. Pass 2: Comprehensive Data Extraction
            final_records = {"source_document": filename, cfg['ROOT_DATA_KEY']: {}}
            
            for section_key, section_content in data_sections.items():
                # Determine the status line index relative to the section header
                first_k = next(iter(section_content.keys()))
                first_line_num = int(first_k.split('_')[1])
                status_line = section_content.get(f"line_{first_line_num + 1}", "")
                
                # Check for "Inactive" or "Cancelled" markers defined in config
                if cfg['CANCEL_MARKER'] in status_line:
                    final_records[cfg['ROOT_DATA_KEY']][section_key] = cfg['CANCEL_MARKER']
                else:
                    # Parse primary metadata
                    entry_data = parse_event_metadata(section_content[first_k], configs)
                    
                    # Map structural and metric data using configured keys
                    entry_data[m_keys['ID']] = int(section_key.split('_')[1])
                    entry_data[m_keys['LOCATION']] = primary_site_id
                    entry_data["Date"][m_keys['TIME']] = get_start_time(section_content, configs)
                    entry_data[m_keys['CLASSIFICATION']] = status_line.strip()
                    entry_data[m_keys['DESCRIPTION']] = get_event_description(section_content, configs)
                    entry_data[m_keys['METRIC']] = get_event_metric(section_content, configs)
                    entry_data[m_keys['VALUE_1']] = get_primary_value(section_content, configs)
                    entry_data[m_keys['VALUE_2']] = get_secondary_value(section_content, configs)
                    
                    # Extract Atmospheric/Environmental Status
                    env_primary, env_val, env_secondary = get_environmental_status(section_content, configs)
                    entry_data.update({
                        m_keys['WEATHER']: env_primary, 
                        m_keys['TEMP']: env_val, 
                        m_keys['CONDITION']: env_secondary
                    })
                    
                    # Extract Participant and Exclusion data
                    entry_data[m_keys['ENTITIES']] = get_participant_data(section_content, configs)
                    entry_data[m_keys['ENTITIES']].extend(get_excluded_entities(section_content, configs))
                    
                    # Extract Summary Totals
                    entry_data[m_keys['AGGREGATE']] = get_aggregate_metric(section_content, configs)
                    
                    # Commit the section record
                    final_records[cfg['ROOT_DATA_KEY']][section_key] = entry_data

            # Apply timeline normalization/enhancement
            enhance_event_times(final_records[cfg['ROOT_DATA_KEY']], configs)
            
            # 4. Storage
            output_filename = f"{Path(filename).stem}{cfg['OUTPUT_SUFFIX']}"
            output_path = os.path.join(output_directory, output_filename)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(final_records, f, indent=4, ensure_ascii=False)

# --- 3. STREAMLIT UI ---
# --- 3. STREAMLIT UI ---

# Updated for 2026: layout="wide" is still standard, 
# but individual components now use width="stretch"
st.set_page_config(page_title="Data Extraction Engine", layout="wide")

st.title("🧩 Text-layer Document Parser")
st.info("This engine uses a JSON-based mapping profile to identify and extract data from unstructured PDFs.")

# Initialize Session State
if 'all_extracted_data' not in st.session_state:
    st.session_state.all_extracted_data = None
if 'table_preview' not in st.session_state:
    st.session_state.table_preview = []

# Sidebar for Config Upload
with st.sidebar:
    st.header("Extraction Profile")
    default_cfg_path = Path("configs.json")
    if default_cfg_path.exists():
        with open(default_cfg_path, "r") as f:
            base_cfg = json.load(f)
    else:
        base_cfg = {}

    uploaded_cfg = st.file_uploader("Upload Profile (JSON)", type="json")
    active_configs = json.load(uploaded_cfg) if uploaded_cfg else base_cfg
    
    if active_configs:
        st.success("Extraction profile active.")
    else:
        st.warning("Please upload or provide a configs.json file.")


# Main File Upload
uploaded_pdfs = st.file_uploader("Upload PDF Documents", type="pdf", accept_multiple_files=True)

# Processing Logic
if uploaded_pdfs and active_configs and st.button("Process Documents"):
    st.session_state.all_extracted_data = {}
    st.session_state.table_preview = []

    for pdf in uploaded_pdfs:
        with st.spinner(f"Analyzing {pdf.name}..."):
            lines = pdf_to_lines(pdf)
            sections = split_content_into_sections(lines, active_configs)
            
            cfg_batch = active_configs['BATCH_PROCESSING']
            loc_guesses = []
            for s_content in sections.values():
                first_k = next(iter(s_content.keys()))
                header = s_content.get(first_k, "")
                if cfg_batch['HEADER_DELIMITER'] in header:
                    raw_loc = header.split(cfg_batch['HEADER_DELIMITER'])[0]
                    loc_guesses.append(get_standardized_location(raw_loc, active_configs))
            
            consensus_loc = Counter(loc_guesses).most_common(1)[0][0] if loc_guesses else "Unknown"

            m_keys = cfg_batch['METADATA_KEYS']
            file_results = {}
            
            for s_key, s_content in sections.items():
                time = get_start_time(s_content, active_configs)
                val = get_primary_value(s_content, active_configs)
                entities = get_participant_data(s_content, active_configs)
                
                # FIX: Add extra data needed for the detailed view here
                weather_info = get_environmental_status(s_content, active_configs)
                aggregate = get_aggregate_metric(s_content, active_configs)
                
                file_results[s_key] = {
                    m_keys['LOCATION']: consensus_loc,
                    m_keys['TIME']: time,
                    m_keys['VALUE_1']: val,
                    m_keys['ENTITIES']: entities,
                    "weather_info": weather_info,
                    m_keys['AGGREGATE']: aggregate
                }
                
                st.session_state.table_preview.append({
                    "File": pdf.name,
                    "Section": s_key,
                    "Location": consensus_loc,
                    "Time": time,
                    "Primary Value": val,
                    "Unit Count": len(entities)
                })
            
            st.session_state.all_extracted_data[pdf.name] = file_results

# --- SHOW RESULTS ---
if st.session_state.all_extracted_data:
    st.divider()
    st.subheader("📊 Extracted Data Overview")
    st.info("💡 **Click a row** in the table to expand and view detailed participant data.")

    df_preview = pd.DataFrame(st.session_state.table_preview)
    m_keys = active_configs['BATCH_PROCESSING']['METADATA_KEYS']

    # Updated: Changed use_container_width=True to width="stretch"
    selection_event = st.dataframe(
        df_preview,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row"
    )

    # 3. Handle the "Expansion" on Click
    if selection_event.selection.rows:
        selected_row_index = selection_event.selection.rows[0]
        row_data = df_preview.iloc[selected_row_index]

        fname = row_data["File"]
        s_id = row_data["Section"]
        full_data = st.session_state.all_extracted_data[fname][s_id]

        st.success(f"🔍 Viewing Details: **{s_id}** at **{full_data[m_keys['LOCATION']]}**")
        
# Display extra info in columns
        det_col1, det_col2, det_col3 = st.columns(3)
        with det_col1:
            val = full_data.get(m_keys['VALUE_1'])
            # Only format with :, if the value is a number (not None)
            display_val = f"${val:,}" if isinstance(val, (int, float)) else "N/A"
            st.metric(m_keys['VALUE_1'], display_val)
            
        with det_col2:
            # Handle potential None from environmental status
            env = full_data.get("weather_info", (None, None, None))
            weather, temp, condition = env if env else ("N/A", "N/A", "N/A")
            st.write(f"**Weather:** {weather} ({temp}°)" if temp else f"**Weather:** {weather}")
            st.write(f"**Condition:** {condition}")
            
        with det_col3:
            agg = full_data.get(m_keys['AGGREGATE'])
            display_agg = f"${agg:,}" if isinstance(agg, (int, float)) else "N/A"
            st.write(f"**Total Pool:** {display_agg}")

        st.markdown("### 📋 Detailed Participant List")

        if full_data[m_keys['ENTITIES']]:
            st.table(pd.DataFrame(full_data[m_keys['ENTITIES']]))
        else:
            st.warning("No entity data found for this section.")

# Download Button
    final_json = json.dumps(st.session_state.all_extracted_data, indent=4)
    st.download_button(
        label="📥 Download Structured JSON",
        data=final_json,
        file_name="extracted_results.json",
        mime="application/json"
    )


if __name__ == "__main__" and not st.runtime.exists():
    import argparse

    # 1. Setup CLI Argument Parsing (This looks very professional on a resume)
    parser = argparse.ArgumentParser(description="Universal Document Extraction Engine")
    
    parser.add_argument("--config", type=str, default="configs.json", help="Path to the mapping configuration")
    parser.add_argument("--input", type=str, default="input_files", help="Folder containing source PDFs")
    parser.add_argument("--output", type=str, default="output_results", help="Folder for processed JSON records")

    args = parser.parse_args()

    # 2. Load the Configuration (Safety Check included)
    try:
        with open(args.config, "r", encoding="utf-8") as f:
            active_configs = json.load(f)
        
        # 3. Trigger the Batch Processor
        # We use the generic name we created in the last step
        process_document_batch(args.input, args.output, active_configs)
        
        print(f"✅ Success: Batch processing complete. Results saved to {args.output}")

    except FileNotFoundError:
        print(f"❌ Error: Configuration file '{args.config}' not found.")
    except Exception as e:
        print(f"❌ Critical Error: {str(e)}")