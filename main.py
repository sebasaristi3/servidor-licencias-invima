"""
Servidor minimo de licencias y receta - Autofill formularios
================================================================
Dos responsabilidades, nada mas por ahora:
  1. Validar si un cliente tiene licencia activa (por su API key).
  2. Si la tiene, entregarle la "receta" (los datos que el programa local
     necesita para funcionar, ej. los IDs de los campos del formulario).

La API key va en el header X-API-Key en cada llamada. Sin una API key
valida y activa, no se entrega nada -- asi el programa local nunca es
util por si solo, aunque alguien copie el .exe/.py y se lo pase a otra
persona.

MVP: los clientes estan en un diccionario en memoria (CLIENTES_ACTIVOS).
Cuando quieras agregar/quitar un cliente, por ahora se edita este archivo
y se vuelve a desplegar. Mas adelante esto pasa a una base de datos real
(Postgres en Railway) para poder manejarlo sin tocar codigo.
"""

from fastapi import FastAPI, HTTPException, Header

app = FastAPI(title="Autofill formularios - Servidor de licencias")

# ------------------------------------------------------------------
# Clientes con licencia activa. Clave = API key (un string largo y
# aleatorio que le das a cada cliente), valor = sus datos.
# ------------------------------------------------------------------
CLIENTES_ACTIVOS = {
    "DEMO-0001-CAMBIAR-ESTA-CLAVE": {
        "empresa": "Cliente de prueba",
        "activo": True,
    },
}

# ------------------------------------------------------------------
# La "receta" del formulario "Agregar Ingrediente" de InvimAgil.
# Son los mismos IDs que hoy estan escritos dentro de
# invima_ingredientes_poc.py (diccionario CAMPOS) -- la idea es que de
# ahora en adelante vivan aqui, no en el archivo que le das al cliente.
# ------------------------------------------------------------------
RECETA_AGREGAR_INGREDIENTE = {
    "boton_anadir": "text=Añadir Ingrediente",
    "tipo": "itemType",
    "ingrediente": "ingredientId",
    "funcion": "functionSlugs",
    "listado_referencia": "referenceListSlug",
    "cantidad": "quantity",
    "unidad_medida": "unitSlug",
    "switch_nanomaterial": "isNanomaterial",
    "boton_guardar": "button:has-text('Guardar')",
}


def validar_cliente(api_key: str) -> dict:
    cliente = CLIENTES_ACTIVOS.get(api_key)
    if not cliente or not cliente.get("activo"):
        raise HTTPException(status_code=403, detail="Licencia no encontrada o inactiva")
    return cliente


@app.get("/")
def estado():
    """Solo para confirmar que el servidor esta vivo (lo puedes abrir en el navegador)."""
    return {"status": "ok", "servicio": "Autofill formularios - Servidor de licencias"}


@app.post("/receta/agregar-ingrediente")
def obtener_receta_ingredientes(x_api_key: str = Header(..., alias="X-API-Key")):
    cliente = validar_cliente(x_api_key)
    return {
        "empresa": cliente["empresa"],
        "campos": RECETA_AGREGAR_INGREDIENTE,
    }
