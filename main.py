
from pathlib import Path
from io import BytesIO
from typing import Literal
from urllib.parse import quote

import gspread
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


# =========================================================
# CONFIGURACION
# =========================================================

BASE = Path(__file__).resolve().parent
JSON_PATH = BASE / "secrets" / "google-drive-credentials.json"

ID_GOOGLE_SHEETS = "1PcVo2saijtf_KHXmeyqI03R4PsdpVP-QMNllkh2e8AQ"
NOMBRE_PESTANA = "Hoja 1"

CARPETA_DRIVE_ID = "1X42jrK4ubBW8SzXQJpMyIuT7YWSaZ5S1"

ALCANCES_GOOGLE = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

COLUMNAS = {
    "unidad": "COD UNIDAD",
    "pedido": "CODIGO PEDIDO",
    "barrio": "BARRIO",
    "nombre": "NOMBRES",
    "estado": "ESTADO",
}

app = FastAPI(title="ASPERSUD | Portal Logístico")

_sheets = None
_drive = None


# =========================================================
# GOOGLE SHEETS
# =========================================================

def obtener_hoja_sheets():
    global _sheets

    if _sheets is not None:
        return _sheets

    if not JSON_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail="No se encuentra secrets/google-drive-credentials.json."
        )

    try:
        credenciales = Credentials.from_service_account_file(
            str(JSON_PATH),
            scopes=ALCANCES_GOOGLE,
        )

        cliente = gspread.authorize(credenciales)
        documento = cliente.open_by_key(ID_GOOGLE_SHEETS)
        _sheets = documento.worksheet(NOMBRE_PESTANA)

        return _sheets

    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo conectar con Google Sheets: {str(e)}"
        )


def normalizar(valor):
    if valor is None:
        return ""

    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)

    return " ".join(str(valor).strip().split()).casefold()


def mostrar_valor(valor):
    if valor is None:
        return ""

    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))

    return str(valor).strip()


def leer_envios():
    try:
        hoja = obtener_hoja_sheets()

        # Lee Google Sheets en cada consulta para obtener los datos actuales.
        datos = hoja.get_all_values()

        if not datos:
            raise HTTPException(
                status_code=500,
                detail="Google Sheets no contiene encabezados."
            )

        encabezados = {
            normalizar(nombre): indice
            for indice, nombre in enumerate(datos[0])
            if nombre is not None and str(nombre).strip()
        }

        indices = {}

        for clave, nombre_columna in COLUMNAS.items():
            indice = encabezados.get(normalizar(nombre_columna))

            if indice is None:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"No se encontró la columna '{nombre_columna}' "
                        "en Google Sheets."
                    )
                )

            indices[clave] = indice

        resultados = []

        for fila in datos[1:]:
            registro = {}

            for clave, indice in indices.items():
                valor = fila[indice] if indice < len(fila) else ""
                registro[clave] = mostrar_valor(valor)

            if any(registro.values()):
                resultados.append(registro)

        return resultados

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Error leyendo Google Sheets: {str(e)}"
        )


# =========================================================
# GOOGLE DRIVE: BUSQUEDA Y DESCARGA DE CONSTANCIAS
# =========================================================

def obtener_drive():
    global _drive

    if _drive is not None:
        return _drive

    if not JSON_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail="No se encuentra el archivo de credenciales de Google."
        )

    try:
        credenciales = Credentials.from_service_account_file(
            str(JSON_PATH),
            scopes=ALCANCES_GOOGLE,
        )

        _drive = build(
            "drive",
            "v3",
            credentials=credenciales,
            cache_discovery=False,
        )

        return _drive

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo conectar con Google Drive: {str(e)}"
        )


def buscar_pdf_drive(registro):
    drive = obtener_drive()

    pedido = str(registro.get("pedido", "")).strip()
    unidad = str(registro.get("unidad", "")).strip()

    codigos = []

    if pedido:
        codigos.append(pedido)

    if unidad and unidad not in codigos:
        codigos.append(unidad)

    for codigo in codigos:
        nombre_pdf = f"{codigo}.pdf"
        nombre_seguro = nombre_pdf.replace("\\", "\\\\").replace("'", "\\'")

        consulta = (
            f"name = '{nombre_seguro}' "
            f"and '{CARPETA_DRIVE_ID}' in parents "
            "and mimeType = 'application/pdf' "
            "and trashed = false"
        )

        try:
            respuesta = drive.files().list(
                q=consulta,
                pageSize=100,
                fields="files(id,name,mimeType)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()

            for archivo in respuesta.get("files", []):
                if archivo.get("name") == nombre_pdf:
                    return archivo

        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Error buscando {nombre_pdf} en Drive: {str(e)}"
            )

    return None


# =========================================================
# MODELO DE BUSQUEDA MULTIPLE
# =========================================================

class ConsultaMultiple(BaseModel):
    campo: Literal["unidad", "pedido", "nombre", "barrio"]
    codigos: list[str]


# =========================================================
# INTERFAZ WEB
# =========================================================

HTML = r"""
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#123747">
<title>ASPERSUD | Portal Logístico</title>

<style>
:root {
    --navy:#123747;
    --navy2:#1b5265;
    --blue:#006184;
    --blue-hover:#004d69;
    --blue-soft:#e8f4f8;
    --green:#087f5b;
    --green-soft:#e7f7ef;
    --red:#b42318;
    --red-soft:#fff0ee;
    --text:#243744;
    --muted:#758895;
    --line:#e1e9ed;
    --bg:#f4f8fa;
    --radius:16px;
    --shadow:0 5px 22px rgba(18,55,71,.045);
}
*{box-sizing:border-box}
body{
    margin:0;background:var(--bg);color:var(--text);
    font-family:"Segoe UI",Arial,sans-serif;font-size:14px;
    -webkit-font-smoothing:antialiased
}
button,input,select,textarea{font:inherit}
button{cursor:pointer}
.topbar{background:var(--navy);color:white;border-bottom:3px solid #2a9bbf}
.topbar-inner{
    max-width:1180px;min-height:82px;margin:auto;padding:17px 26px;
    display:flex;justify-content:space-between;align-items:center;gap:20px
}
.brand{display:flex;align-items:center;gap:13px}
.brand-icon{
    width:43px;height:43px;flex:0 0 43px;border-radius:12px;
    background:#ffffff1a;display:grid;place-items:center;font-size:21px;
    font-weight:800;border:1px solid #ffffff1f
}
.brand-name{font-size:18px;font-weight:800;letter-spacing:.45px}
.brand-subtitle{color:#b8c6dc;margin-top:4px;font-size:12px}
.topbar-label{font-size:11px;letter-spacing:1px;font-weight:750;color:#c5d4ea;text-align:right}
.topbar-label span{display:block;color:#91d9b8;margin-top:6px;letter-spacing:0;font-size:12px}
main{width:min(1120px,calc(100% - 40px));margin:30px auto 45px}
.page-heading{
    display:flex;align-items:flex-end;justify-content:space-between;
    gap:18px;margin-bottom:22px
}
.eyebrow{color:var(--blue);text-transform:uppercase;letter-spacing:1.3px;font-size:10px;font-weight:800;margin-bottom:8px}
h1{margin:0;font-size:clamp(24px,3vw,31px);letter-spacing:-.7px;line-height:1.2}
.page-description{margin:9px 0 0;color:var(--muted);line-height:1.6;max-width:650px}
.heading-chip{
    background:white;border:1px solid var(--line);color:#526078;
    border-radius:10px;padding:10px 13px;white-space:nowrap;font-size:12px;font-weight:650
}
.layout{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(300px,.85fr);gap:20px;align-items:start}
.panel{background:white;border:1px solid var(--line);border-radius:var(--radius);padding:23px;box-shadow:var(--shadow);min-width:0}
.panel-heading{display:flex;align-items:flex-start;gap:12px;margin-bottom:20px}
.panel-icon{
    width:39px;height:39px;flex:0 0 39px;display:grid;place-items:center;
    border-radius:11px;background:var(--blue-soft);color:var(--blue);font-size:19px;font-weight:800
}
.panel-heading h2{margin:1px 0 5px;font-size:16px}
.panel-heading p{margin:0;color:var(--muted);font-size:12px;line-height:1.5}
.field{margin-bottom:16px}
label{display:block;color:#46536a;font-size:12px;font-weight:750;margin-bottom:8px}
input,select,textarea{
    display:block;width:100%;border:1px solid #dce3ed;background:white;
    border-radius:10px;padding:12px 13px;color:var(--text);outline:none;
    transition:border-color .15s,box-shadow .15s
}
input,select{min-height:44px}
textarea{min-height:132px;line-height:1.65;resize:vertical}
input::placeholder,textarea::placeholder{color:#a0aabc}
input:focus,select:focus,textarea:focus{border-color:#6c91ed;box-shadow:0 0 0 3px #3268df1a}
.field-hint{color:#929daf;font-size:11px;margin-top:7px;line-height:1.45}
.button{
    min-height:43px;display:inline-flex;align-items:center;justify-content:center;
    gap:8px;padding:11px 16px;border:1px solid transparent;border-radius:10px;
    font-weight:750;font-size:13px;transition:background .15s,border-color .15s
}
.button:disabled{opacity:.65;cursor:wait}
.button-primary{background:var(--blue);color:white}
.button-primary:hover{background:var(--blue-hover)}
.button-light{background:#f4f6fa;color:#4a5870;border-color:var(--line)}
.button-light:hover{background:#eaf0f8}
.button-full{width:100%}
.button-row{display:flex;gap:9px;flex-wrap:wrap}
.search-note{
    display:flex;gap:9px;align-items:flex-start;border-top:1px solid var(--line);
    margin-top:19px;padding-top:15px;color:var(--muted);font-size:11px;line-height:1.55
}
.search-note strong{color:#55637a}
.results-panel{margin-top:21px}
.results-header{
    display:flex;align-items:center;justify-content:space-between;
    flex-wrap:wrap;gap:12px;padding-bottom:16px;border-bottom:1px solid var(--line)
}
.results-title{display:flex;align-items:center;gap:11px;flex-wrap:wrap}
.results-title h2{margin:0;font-size:17px}
.results-count{
    display:inline-flex;align-items:center;min-height:25px;padding:4px 9px;
    border-radius:7px;background:#f0f3f8;color:#5b687d;font-size:11px;font-weight:750
}
.results-content{padding-top:4px}
.empty-state{text-align:center;padding:36px 18px 29px}
.empty-icon{
    width:53px;height:53px;display:grid;place-items:center;border-radius:16px;
    background:#f0f4fb;color:#7083a4;font-size:24px;margin:0 auto 15px
}
.empty-state h3{font-size:14px;margin:0 0 7px}
.empty-state p{color:var(--muted);margin:0 auto;max-width:370px;line-height:1.6;font-size:12px}
.shipment-card{border:1px solid var(--line);border-radius:13px;margin-top:15px;overflow:hidden;background:white}
.shipment-card:hover{border-color:#cbd8ef;box-shadow:0 4px 14px #1e3c640b}
.shipment-card-top{padding:17px 18px 14px;display:flex;align-items:flex-start;justify-content:space-between;gap:15px}
.shipment-name{font-size:15px;font-weight:750;line-height:1.45;overflow-wrap:anywhere}
.shipment-caption{color:var(--muted);font-size:11px;margin-top:5px}
.status-pill{
    display:inline-flex;align-items:center;gap:5px;flex:0 0 auto;max-width:150px;
    overflow-wrap:anywhere;border-radius:7px;padding:6px 8px;background:var(--green-soft);
    color:var(--green);font-size:10px;font-weight:800
}
.status-pill.other{background:#f0f3f8;color:#58667c}
.shipment-details{padding:0 18px 17px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.detail-box{min-width:0;padding:11px 12px;border:1px solid #edf0f5;border-radius:9px;background:#fafbfd}
.detail-label{color:#8591a4;text-transform:uppercase;letter-spacing:.55px;font-size:9px;font-weight:800;margin-bottom:6px}
.detail-value{color:#344158;font-size:12px;font-weight:700;line-height:1.5;overflow-wrap:anywhere}
.shipment-actions{
    border-top:1px solid #edf0f5;background:#fcfdff;padding:12px 18px;
    display:flex;align-items:center;gap:10px;flex-wrap:wrap
}
.shipment-actions .button{min-height:37px;padding:9px 13px;font-size:12px}
.constancia-message{min-width:0;flex:1;color:var(--muted);font-size:11px;line-height:1.5;overflow-wrap:anywhere}
.constancia-message.error{color:var(--red)}
.notice{padding:12px 14px;border-radius:9px;background:#f5f7fb;color:#69768b;margin-top:14px;font-size:12px;line-height:1.55;overflow-wrap:anywhere}
.notice.error{color:var(--red);background:var(--red-soft)}
.footer{text-align:center;padding:23px 10px 0;color:#929daf;font-size:11px;line-height:1.6}
@media(max-width:780px){
    main{width:min(calc(100% - 28px),650px);margin-top:23px}
    .layout{grid-template-columns:1fr}
    .topbar-inner{padding:15px 18px}
    .topbar-label{display:none}
    .page-heading{align-items:flex-start;flex-direction:column}
    .heading-chip{white-space:normal}
}
@media(max-width:480px){
    main{width:calc(100% - 20px);margin-top:17px}
    .topbar-inner{padding:14px 12px}
    .brand-icon{width:38px;height:38px;flex-basis:38px}
    .brand-name{font-size:15px}
    .brand-subtitle{font-size:11px}
    .panel{padding:17px;border-radius:13px}
    .shipment-card-top{padding:14px 13px 12px}
    .shipment-details{padding:0 13px 13px;gap:8px}
    .detail-box{padding:9px}
    .shipment-actions{padding:11px 13px}
    .shipment-actions .button{width:100%}
    .constancia-message{flex-basis:100%}
}
</style>
</head>

<body>
<header class="topbar">
    <div class="topbar-inner">
        <div class="brand">
            <div class="brand-icon">A</div>
            <div>
                <div class="brand-name">ASPERSUD</div>
                <div class="brand-subtitle">Portal de consulta logística</div>
            </div>
        </div>
        <div class="topbar-label">
            SISTEMA DE CONSULTA
            <span>● Portal disponible</span>
        </div>
    </div>
</header>

<main>
    <section class="page-heading">
        <div>
            <div class="eyebrow">Operaciones · Trazabilidad</div>
            <h1>Consulta de envíos</h1>
            <p class="page-description">
                Localiza pedidos, consulta su estado y accede a las
                constancias de entrega almacenadas en Google Drive.
            </p>
        </div>
        <div class="heading-chip">⌕ &nbsp; Consulta individual y múltiple</div>
    </section>

    <div class="layout">
        <section class="panel">
            <div class="panel-heading">
                <div class="panel-icon">⌕</div>
                <div>
                    <h2>Buscar un envío</h2>
                    <p>Consulta un registro por su código o información del destinatario.</p>
                </div>
            </div>

            <form id="formBusqueda">
                <div class="field">
                    <label for="campo">CRITERIO DE BÚSQUEDA</label>
                    <select id="campo">
                        <option value="pedido">Código de pedido</option>
                        <option value="unidad">Código de unidad</option>
                        <option value="nombre">Nombre del destinatario</option>
                        <option value="barrio">Barrio</option>
                    </select>
                </div>

                <div class="field">
                    <label for="valor">DATO DE BÚSQUEDA</label>
                    <input id="valor" placeholder="Ej. 1800388"
                           autocomplete="off" required>
                    <div class="field-hint">
                        Puedes ingresar un código completo o una parte del nombre o barrio.
                    </div>
                </div>

                <button class="button button-primary button-full"
                        id="btnBuscar" type="submit">
                    ⌕ &nbsp; Buscar envío
                </button>
            </form>

            <div class="search-note">
                <span>ⓘ</span>
                <div>
                    <strong>Datos consultados desde Google Sheets.</strong>
                    La constancia se busca por código de pedido y, si no se encuentra,
                    por código de unidad en la carpeta configurada de Google Drive.
                </div>
            </div>
        </section>

        <section class="panel">
            <div class="panel-heading">
                <div class="panel-icon">▤</div>
                <div>
                    <h2>Búsqueda múltiple</h2>
                    <p>Consulta varios pedidos en una sola operación.</p>
                </div>
            </div>

            <form id="formMultiple">
                <div class="field">
                    <label for="campoMultiple">CRITERIO DE BÚSQUEDA</label>
                    <select id="campoMultiple">
                        <option value="pedido">Código de pedido</option>
                        <option value="unidad">Código de unidad</option>
                        <option value="nombre">Nombre del destinatario</option>
                        <option value="barrio">Barrio</option>
                    </select>
                </div>

                <div class="field">
                    <label for="valoresMultiple">LISTA DE CÓDIGOS O TÉRMINOS</label>
                    <textarea id="valoresMultiple"
                        placeholder="1800388&#10;177490&#10;2088851"></textarea>
                    <div class="field-hint">
                        Coloca un código o término por línea. Los resultados se mostrarán juntos.
                    </div>
                </div>

                <div class="button-row">
                    <button class="button button-primary" id="btnMultiple" type="submit">
                        Buscar varios
                    </button>
                    <button class="button button-light" id="limpiarMultiple" type="button">
                        Limpiar
                    </button>
                </div>
            </form>
        </section>
    </div>

    <section class="panel results-panel">
        <div class="results-header">
            <div class="results-title">
                <h2>Resultados de consulta</h2>
                <span class="results-count" id="contador">Sin búsqueda</span>
            </div>
        </div>

        <div class="results-content" id="resultados">
            <div class="empty-state">
                <div class="empty-icon">⌕</div>
                <h3>Todo listo para consultar</h3>
                <p>
                    Introduce un código de pedido, código de unidad,
                    nombre o barrio para visualizar los datos del envío
                    y abrir su constancia de entrega.
                </p>
            </div>
        </div>
    </section>

    <footer class="footer">
        ASPERSUD · Portal de consulta logística<br>
        Información obtenida desde Google Sheets y constancias de Google Drive.
    </footer>
</main>

<script>
const $ = id => document.getElementById(id);
let ultimaBusqueda = 0;

function escapar(texto) {
    return String(texto ?? "").replace(/[&<>"']/g, letra => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
    })[letra]);
}

function mostrarVacio(titulo, descripcion, error = false) {
    $("resultados").innerHTML = `
        <div class="empty-state">
            <div class="empty-icon">${error ? "!" : "⌕"}</div>
            <h3>${escapar(titulo)}</h3>
            <p>${escapar(descripcion)}</p>
        </div>`;
}

function mostrarError(texto) {
    $("resultados").innerHTML =
        `<div class="notice error">${escapar(texto)}</div>`;
}

function pintar(registros, sinCoincidencia = []) {
    $("contador").textContent = `${registros.length} resultado(s)`;

    if (!registros.length) {
        mostrarVacio(
            "No encontramos coincidencias",
            "Verifica el dato ingresado o prueba con otro criterio de búsqueda."
        );
        return;
    }

    let html = "";

    for (const r of registros) {
        const unidad = String(r.unidad ?? "");
        const pedido = String(r.pedido ?? "");
        const estado = String(r.estado ?? "").trim();
        const entregado = estado.toUpperCase() === "ENTREGADO";

        html += `
        <article class="shipment-card">
            <div class="shipment-card-top">
                <div>
                    <div class="shipment-name">
                        ${escapar(r.nombre || "Destinatario no registrado")}
                    </div>
                    <div class="shipment-caption">Registro de envío · ASPERSUD</div>
                </div>
                <span class="status-pill ${entregado ? "" : "other"}">
                    ${entregado ? "● " : ""}${escapar(estado || "SIN ESTADO")}
                </span>
            </div>

            <div class="shipment-details">
                <div class="detail-box">
                    <div class="detail-label">Código de pedido</div>
                    <div class="detail-value">${escapar(pedido || "—")}</div>
                </div>
                <div class="detail-box">
                    <div class="detail-label">Código de unidad</div>
                    <div class="detail-value">${escapar(unidad || "—")}</div>
                </div>
                <div class="detail-box">
                    <div class="detail-label">Barrio</div>
                    <div class="detail-value">${escapar(r.barrio || "—")}</div>
                </div>
                <div class="detail-box">
                    <div class="detail-label">Estado del envío</div>
                    <div class="detail-value">${escapar(estado || "No registrado")}</div>
                </div>
            </div>

            <div class="shipment-actions">
                <button type="button" class="button button-primary btnConstancia"
                    data-unidad="${escapar(unidad)}"
                    data-pedido="${escapar(pedido)}">
                    ↗ &nbsp; Ver constancia
                </button>
                <span class="constancia-message"></span>
            </div>
        </article>`;
    }

    if (sinCoincidencia.length) {
        html += `<div class="notice">
            <strong>Sin coincidencia:</strong>
            ${sinCoincidencia.map(escapar).join(", ")}
        </div>`;
    }

    $("resultados").innerHTML = html;

    document.querySelectorAll(".btnConstancia").forEach(boton => {
        boton.addEventListener("click", async () => {
            await abrirConstancia(
                boton,
                boton.dataset.unidad,
                boton.dataset.pedido
            );
        });
    });
}

async function abrirConstancia(boton, unidad, pedido) {
    const mensaje = boton.parentElement.querySelector(".constancia-message");
    const ventana = window.open("about:blank", "_blank");

    if (!ventana) {
        mensaje.textContent = "Permite las ventanas emergentes para abrir la constancia.";
        mensaje.classList.add("error");
        return;
    }

    ventana.document.title = "Cargando constancia...";
    ventana.document.body.innerHTML =
        "<p style='font-family:Arial,sans-serif;padding:24px;color:#344158'>Buscando constancia en Google Drive...</p>";

    boton.disabled = true;
    mensaje.textContent = "Buscando PDF en Drive...";
    mensaje.classList.remove("error");

    try {
        const parametros = new URLSearchParams();
        if (pedido) parametros.set("pedido", pedido);
        if (unidad) parametros.set("unidad", unidad);

        const respuesta = await fetch(`/api/constancia?${parametros.toString()}`);

        if (!respuesta.ok) {
            let detalle = "No se pudo obtener la constancia.";
            try {
                const data = await respuesta.json();
                detalle = data.detail || detalle;
            } catch (_) {}
            throw new Error(detalle);
        }

        const blob = await respuesta.blob();
        const urlPdf = URL.createObjectURL(blob);

        ventana.location.replace(urlPdf);
        mensaje.textContent = "Constancia abierta correctamente.";

        setTimeout(() => URL.revokeObjectURL(urlPdf), 3600000);

    } catch (error) {
        ventana.close();
        mensaje.textContent = error.message || "Error al abrir la constancia.";
        mensaje.classList.add("error");
    } finally {
        boton.disabled = false;
    }
}

$("formBusqueda").addEventListener("submit", async evento => {
    evento.preventDefault();

    const campo = $("campo").value;
    const valor = $("valor").value.trim();
    if (!valor) return;

    const idActual = ++ultimaBusqueda;

    $("btnBuscar").disabled = true;
    $("contador").textContent = "Buscando...";
    mostrarVacio("Consultando envíos", "Buscando datos actualizados en Google Sheets.");

    try {
        const parametros = new URLSearchParams({campo, valor});
        const respuesta = await fetch(`/api/search?${parametros.toString()}`);
        const data = await respuesta.json();

        if (!respuesta.ok) {
            throw new Error(data.detail || "Error durante la búsqueda.");
        }

        if (idActual !== ultimaBusqueda) return;
        pintar(data.resultados || []);

    } catch (error) {
        if (idActual === ultimaBusqueda) {
            $("contador").textContent = "Error";
            mostrarError(error.message);
        }
    } finally {
        $("btnBuscar").disabled = false;
    }
});

$("formMultiple").addEventListener("submit", async evento => {
    evento.preventDefault();

    const campo = $("campoMultiple").value;
    const codigos = $("valoresMultiple").value
        .split(/\r?\n/)
        .map(valor => valor.trim())
        .filter(Boolean);

    if (!codigos.length) {
        mostrarError("Introduce al menos un código o término, uno por línea.");
        return;
    }

    $("btnMultiple").disabled = true;
    $("contador").textContent = "Buscando...";
    mostrarVacio("Procesando consulta múltiple", "Consultando Google Sheets.");

    try {
        const respuesta = await fetch("/api/bulk", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({campo, codigos})
        });

        const data = await respuesta.json();

        if (!respuesta.ok) {
            throw new Error(data.detail || "Error durante la búsqueda.");
        }

        pintar(data.resultados || [], data.sin_coincidencia || []);

    } catch (error) {
        $("contador").textContent = "Error";
        mostrarError(error.message);
    } finally {
        $("btnMultiple").disabled = false;
    }
});

$("limpiarMultiple").addEventListener("click", () => {
    $("valoresMultiple").value = "";
    $("valoresMultiple").focus();
});
</script>
</body>
</html>
"""


# =========================================================
# RUTAS DEL PORTAL
# =========================================================

@app.get("/", response_class=HTMLResponse)
def inicio():
    return HTML


@app.get("/health")
def health():
    return {
        "status": "ok",
        "fuente_datos": "Google Sheets",
        "documento_id": ID_GOOGLE_SHEETS,
        "pestana": NOMBRE_PESTANA,
        "credentials_exists": JSON_PATH.exists(),
    }


@app.get("/api/search")
def buscar_envio(
    campo: Literal["unidad", "pedido", "nombre", "barrio"] = Query(...),
    valor: str = Query(..., min_length=1),
):
    termino = normalizar(valor)

    if not termino:
        return {"resultados": []}

    envios = leer_envios()

    resultados = [
        registro for registro in envios
        if termino in normalizar(registro.get(campo, ""))
    ]

    return {"resultados": resultados}


@app.post("/api/bulk")
def buscar_varios(consulta: ConsultaMultiple):
    terminos = []
    vistos = set()

    for valor in consulta.codigos:
        limpio = str(valor).strip()
        clave = normalizar(limpio)

        if limpio and clave not in vistos:
            terminos.append(limpio)
            vistos.add(clave)

    envios = leer_envios()
    encontrados = []
    claves_encontradas = set()
    sin_coincidencia = []

    for termino in terminos:
        coincidencias = [
            registro for registro in envios
            if normalizar(termino) in normalizar(
                registro.get(consulta.campo, "")
            )
        ]

        if not coincidencias:
            sin_coincidencia.append(termino)
            continue

        for registro in coincidencias:
            clave = (
                registro.get("unidad", ""),
                registro.get("pedido", ""),
            )

            if clave not in claves_encontradas:
                claves_encontradas.add(clave)
                encontrados.append(registro)

    return {
        "resultados": encontrados,
        "sin_coincidencia": sin_coincidencia,
    }


@app.get("/api/constancia")
def obtener_constancia(
    pedido: str = Query(default=""),
    unidad: str = Query(default=""),
):
    pedido = pedido.strip()
    unidad = unidad.strip()

    if not pedido and not unidad:
        raise HTTPException(
            status_code=400,
            detail="Debes proporcionar un código de pedido o de unidad."
        )

    archivo = buscar_pdf_drive({
        "pedido": pedido,
        "unidad": unidad,
    })

    if not archivo:
        nombres = []

        if pedido:
            nombres.append(f"{pedido}.pdf")

        if unidad and unidad != pedido:
            nombres.append(f"{unidad}.pdf")

        raise HTTPException(
            status_code=404,
            detail=(
                "No se encontró la constancia en la carpeta configurada. "
                "Nombres buscados: " + ", ".join(nombres)
            )
        )

    try:
        drive = obtener_drive()

        solicitud = drive.files().get_media(
            fileId=archivo["id"],
            supportsAllDrives=True,
        )

        contenido = BytesIO()
        descarga = MediaIoBaseDownload(contenido, solicitud)
        terminado = False

        while not terminado:
            _, terminado = descarga.next_chunk()

        contenido.seek(0)
        nombre_seguro = quote(archivo.get("name", "constancia.pdf"))

        return StreamingResponse(
            contenido,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f"inline; filename*=UTF-8''{nombre_seguro}"
                ),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Se encontró el PDF, pero no se pudo descargar: {str(e)}"
        )
