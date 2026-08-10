import json
import hashlib
import os
from datetime import datetime

# -------------------------------------------------------------------------
# CONFIGURACIÓN DE SEGURIDAD (Edge Instance - Hospital Central)
# -------------------------------------------------------------------------
SALT_ROTATIVO_ACTUAL = "Mendoza_Hospital_Central_Secret_2026_Q2"

def aplicar_ceguera_tecnica(id_real, salt):
    """Aplica SHA-256 + Salt Rotativo para garantizar la privacidad (Capa Silver)."""
    if not id_real:
        return "ANONIMO"
    id_clean = str(id_real).strip()
    input_cripto = f"{id_clean}{salt}".encode('utf-8')
    return hashlib.sha256(input_cripto).hexdigest()

def calcular_edad(fecha_nacimiento):
    if not fecha_nacimiento:
        return None
    try:
        fecha_clean = str(fecha_nacimiento).split()[0].strip()
        birth_date = datetime.strptime(fecha_clean, "%Y-%m-%d")
        today = datetime.now()
        return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    except Exception:
        return None

def normalizar_valor_unico(valor):
    if isinstance(valor, list):
        return str(valor[0]).strip() if valor else None
    return str(valor).strip() if valor else None

# -------------------------------------------------------------------------
# EXTRACTORES Y VALIDACIÓN DE ONTOLOGÍAS (SNOMED-CT / LOINC / CIE-10)
# -------------------------------------------------------------------------
def extraer_todos_los_recursos(bundle):
    """Extrae y auto-sana los recursos médicos desde el FHIR."""
    recursos = []
    for entry in bundle.get("entry", []):
        if isinstance(entry, dict) and "resource" in entry:
            res = entry["resource"].copy()
            if "resourceType" not in res:
                full_url = str(entry.get("fullUrl", "")).lower()
                id_val = str(res.get("id", "")).lower()
                if "medication" in full_url or "medication" in id_val:
                    res["resourceType"] = "MedicationRequest"
                elif "condition" in full_url or "condition" in id_val:
                    res["resourceType"] = "Condition"
                elif "patient" in full_url or "paciente" in id_val:
                    res["resourceType"] = "Patient"
            recursos.append(res)
            
    resource_field = bundle.get("resource")
    if isinstance(resource_field, list):
        for res in resource_field:
            if isinstance(res, dict):
                recursos.append(res)
    elif isinstance(resource_field, dict):
        recursos.append(resource_field)
        
    return recursos

def buscar_y_validar_ontologia(resource):
    """Busca el CIE-10/SNOMED y valida su integridad para la Capa Silver."""
    codigo = None
    descripcion = None
    sistema = "Desconocido"
    
    code_data = resource.get("code", {})
    if isinstance(code_data, dict):
        codings = code_data.get("coding", [])
        if codings and isinstance(codings, list):
            codigo = normalizar_valor_unico(codings[0].get("code"))
            descripcion = codings[0].get("display", code_data.get("text"))
            sistema_crudo = str(codings[0].get("system", "")).lower()
            if "snomed" in sistema_crudo: sistema = "SNOMED-CT"
            elif "cie" in sistema_crudo or "icd" in sistema_crudo: sistema = "CIE-10"
            elif "loinc" in sistema_crudo: sistema = "LOINC"

    if not codigo:
        for ext in resource.get("extension", []):
            val = ext.get("valueString", "")
            if "C50" in val or "C34" in val:
                codigo = val.split(",")[0].strip() if "," in val else val
                descripcion = val
                sistema = "CIE-10 (Inferido)"
                
    return codigo, descripcion, sistema

def deducir_nombre_droga(resource):
    med_code = resource.get("medicationCodeableConcept", {})
    if isinstance(med_code, dict):
        codings = med_code.get("coding", [])
        if codings and codings[0].get("display"):
            return codings[0].get("display")
        if med_code.get("text"):
            return med_code.get("text")
            
    for ext in resource.get("extension", []):
        if "supporting-information" in ext.get("url", "") or "valueString" in ext:
            return ext.get("valueString")
                
    return "Terapia Sistémica No Especificada"

# -------------------------------------------------------------------------
# PROCESO ETL PRINCIPAL (MEDALLION ARCHITECTURE)
# -------------------------------------------------------------------------
def ejecutar_pipeline_etl():
    print("==================================================================")
    print("-> INICIANDO PIPELINE ETL DE CIFRADO (ARQUITECTURA MEDALLION)")
    print("==================================================================")
    
    # 0. Preparar Directorios Medallion
    for capa in ['bronze', 'silver', 'gold']:
        os.makedirs(capa, exist_ok=True)
        print(f"📁 Directorio verificado: /{capa}")

    # 1. CAPA BRONZE (Ingesta cruda)
    archivo_origen = "dataset_sintetico_FHIR.json"
    try:
        with open(archivo_origen, "r", encoding="utf-8") as f:
            bundles_crudos = json.load(f)
        
        # Guardamos una copia inmutable en Bronze
        with open("bronze/raw_fhir_batch.json", "w", encoding="utf-8") as f:
            json.dump(bundles_crudos, f, indent=4)
        print(f"🥉 [BRONZE] Ingesta de datos crudos FHIR completada. {len(bundles_crudos)} registros guardados.")
    except FileNotFoundError:
        print(f"❌ Error: No se encontró '{archivo_origen}'.")
        return

    registros_silver = []
    registros_gold = []

    # 2. CAPA SILVER (Procesamiento, Validación de Ontologías y Cifrado)
    for index, bundle in enumerate(bundles_crudos, 1):
        datos_paciente = {"id_real": None, "nacimiento": None, "edad": None}
        datos_diagnostico = {"codigo": None, "descripcion": None, "sistema_ontologico": None}
        datos_tratamiento = {"droga": None}

        recursos = extraer_todos_los_recursos(bundle)
        
        for res in recursos:
            tipo = res.get("resourceType")
            if tipo == "Patient":
                datos_paciente["id_real"] = normalizar_valor_unico(res.get("id"))
                datos_paciente["nacimiento"] = res.get("birthDate")
                datos_paciente["edad"] = calcular_edad(datos_paciente["nacimiento"])
            elif tipo == "Condition":
                cod, desc, sis = buscar_y_validar_ontologia(res)
                if cod: 
                    datos_diagnostico["codigo"] = cod
                    datos_diagnostico["descripcion"] = desc
                    datos_diagnostico["sistema_ontologico"] = sis
            elif tipo == "MedicationRequest":
                datos_tratamiento["droga"] = deducir_nombre_droga(res)

        if not datos_paciente["id_real"]:
            datos_paciente["id_real"] = normalizar_valor_unico(bundle.get("id"))

        if datos_paciente["id_real"]:
            # Ceguera Técnica Total aplicada en la Capa Silver
            id_anonimo = aplicar_ceguera_tecnica(datos_paciente["id_real"], SALT_ROTATIVO_ACTUAL)
            
            # Clasificación Oncológica
            tipo_cancer = "No Especificado"
            cie = (datos_diagnostico["codigo"] or "").upper()
            desc = (datos_diagnostico["descripcion"] or "").lower()
            
            if "C50" in cie or "breast" in desc or "mama" in desc:
                tipo_cancer = "Cáncer de Mama"
            elif "C34" in cie or "lung" in desc or "pulmon" in desc or "nsclc" in desc:
                tipo_cancer = "Cáncer de Pulmón (NSCLC)"
            elif "sarcoma" in desc or "uterus" in desc:
                tipo_cancer = "Sarcoma de Útero"

            # Registro Silver (Estandarizado pero con meta-datos de auditoría)
            registro_silver = {
                "patient_hash_sha256": id_anonimo,
                "metadata_auditoria": {
                    "ontologia_validada": datos_diagnostico["sistema_ontologico"],
                    "fecha_procesamiento": datetime.now().isoformat()
                },
                "clinica": {
                    "cancer": tipo_cancer,
                    "codigo_enfermedad": datos_diagnostico["codigo"],
                    "descripcion": datos_diagnostico["descripcion"]
                },
                "demografia": {
                    "edad": datos_paciente["edad"]
                },
                "tratamiento": datos_tratamiento["droga"]
            }
            registros_silver.append(registro_silver)

            # Registro Gold (Plano, optimizado para RAG y métricas predictivas)
            registro_gold = {
                "patient_hash_sha256": id_anonimo,
                "datos_demograficos": {
                    "edad_estimada": datos_paciente["edad"],
                    "anio_nacimiento": datos_paciente["nacimiento"][:4] if datos_paciente["nacimiento"] else None
                },
                "datos_clinicos": {
                    "tipo_cancer": tipo_cancer,
                    "cie10_diagnostico": datos_diagnostico["codigo"],
                    "diagnostico_descripcion": datos_diagnostico["descripcion"] or "Evaluación oncológica registrada"
                },
                "datos_tratamiento": {
                    "droga_prescripta": datos_tratamiento["droga"] or "Esquema sistémico indicado"
                }
            }
            registros_gold.append(registro_gold)
            
            print(f"   [OK] Procesado -> Hash: {id_anonimo[:8]}... | Ontología: {datos_diagnostico['sistema_ontologico']}")

    # 3. GUARDADO DE CAPAS SILVER Y GOLD
    if registros_silver and registros_gold:
        with open("silver/anonymized_records.json", "w", encoding="utf-8") as f:
            json.dump(registros_silver, f, indent=4, ensure_ascii=False)
        print(f"\n🥈 [SILVER] Limpieza de anomalías y Ceguera Técnica aplicadas. Archivo generado en /silver.")

        with open("gold/dataset_estructurado_seguro.json", "w", encoding="utf-8") as f:
            json.dump(registros_gold, f, indent=4, ensure_ascii=False)
        print(f"🥇 [GOLD] Datos estructurados listos para cálculos de toxicidad en RAG. Archivo generado en /gold.")
            
        print("\n==================================================================")
        print("--- ¡PIPELINE MEDALLION FINALIZADO CON ÉXITO! ---")
        print("==================================================================")
    else:
        print("\n❌ Error: No se pudo anonimizar ningún registro.")

if __name__ == "__main__":
    ejecutar_pipeline_etl()