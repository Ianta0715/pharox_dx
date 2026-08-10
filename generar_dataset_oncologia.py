import requests
import json
import time

URL_OLLAMA = "http://localhost:11434/api/generate"

def limpiar_y_extraer_json(texto):
    if not texto:
        return None
    texto = texto.strip()
    
    # 1. Intentar limpiar bloques de código markdown ```json ... ``` o ``` ... ```
    if "```" in texto:
        partes = texto.split("```")
        for parte in partes:
            parte_limpia = parte.strip()
            if parte_limpia.startswith("json"):
                parte_limpia = parte_limpia[4:].strip()
            try:
                json.loads(parte_limpia)
                return parte_limpia
            except Exception:
                continue

    # 2. Intentar buscar el primer '{' o '[' y el último '}' o ']'
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start_idx = texto.find(start_char)
        end_idx = texto.rfind(end_char)
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            candidato = texto[start_idx:end_idx+1]
            try:
                json.loads(candidato)
                return candidato
            except Exception:
                pass
                
    return texto

def generar_paciente_oncologico_fhir(id_falso):
    prompt_sistema = f"""
    Actúa como un generador automático de datos médicos sintéticos para pruebas de sistemas.
    Inventa un paciente ficticio con Cáncer de Pulmón (NSCLC) o Cáncer de Mama.
    El resultado debe ser un único objeto JSON que simule un "Bundle" o una lista de recursos bajo el estándar FHIR internacional.
    
    Debes incluir estrictamente estos 3 recursos dentro del JSON:
    1. "Patient": ID 'paciente-{id_falso}', nombre ficticio, edad aleatoria.
    2. "Condition": Diagnóstico oncológico codificado con CIE-10 (ej. C50.9 para mama o C34.9 para pulmón).
    3. "MedicationRequest": Una droga oncológica aprobada (ej. Osimertinib, Pembrolizumab o T-DXd) con su línea de tratamiento.

    Devolvé ÚNICAMENTE el objeto JSON crudo. Sin introducciones, sin texto explicativo, sin marcas de bloque de código (```json). Solo el JSON válido.
    """

    payload = {
        "model": "phi3",
        "prompt": prompt_sistema,
        "format": "json",  # Fuerza a Ollama a retornar un JSON válido
        "stream": False,
        "options": {
            "temperature": 0.7, # Subimos la temperatura para que cada paciente sea diferente e invente casos distintos
            "num_predict": 1500
        }
    }

    try:
        response = requests.post(URL_OLLAMA, json=payload)
        if response.status_code == 200:
            return response.json()['response']
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None

# Simulamos la generación de un lote (Dataset) de 5 pacientes de prueba
dataset_sintetico = []
print("-> Iniciando la fábrica de datos sintéticos oncológicos...")

for i in range(1, 6):
    print(f"Generando paciente de prueba oncológico {i}/5...")
    paciente_raw = generar_paciente_oncologico_fhir(i)
    paciente_json_str = limpiar_y_extraer_json(paciente_raw)
    
    if paciente_json_str:
        try:
            # Lo convertimos a diccionario de Python para verificar que sea JSON válido
            paciente_data = json.loads(paciente_json_str)
            dataset_sintetico.append(paciente_data)
        except Exception as e:
            print(f"❌ El paciente {i} no se generó en un JSON limpio. Error al decodificar: {e}. Saltando...")
            # Si queremos debugear qué devolvió:
            # print(f"Raw recibido: {paciente_raw}")
    else:
        print(f"❌ El paciente {i} devolvió vacío o inválido. Saltando...")
    time.sleep(1) # Le damos un respiro al contenedor

# Guardamos el lote completo en un archivo listo para tu ETL
with open("dataset_sintetico_FHIR.json", "w", encoding="utf-8") as f:
    json.dump(dataset_sintetico, f, indent=4, ensure_ascii=False)

print("\n--- ¡DATASET SINTÉTICO CREADO CON ÉXITO! ---")
print(f"Se guardaron {len(dataset_sintetico)} pacientes en 'dataset_sintetico_FHIR.json'")