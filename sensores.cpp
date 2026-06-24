#include <Arduino.h>

// ── Pines ─────────────────────────────────────────────────
const int PIN_MIC    = 27;
const int PIN_BUZZER = 26;
const int PIN_POT    = 35;  // Solo entrada, no necesita modo OUTPUT
const int PIN_TMP36  = 34;  // Solo entrada

// ── Umbrales ──────────────────────────────────────────────
const int   BPM_ALTO      = 100;
const float TEMP_MIN_BEBE = 20.0;
const float TEMP_MAX_BEBE = 22.0;

// ── Variables BPM simulado ────────────────────────────────
int bpmSimulado = 0;

// ── Variables temperatura ─────────────────────────────────
float temperatura = 0.0;
unsigned long ultimaLecturaTemp = 0;
const int INTERVALO_TEMP = 2000;

// ── Alertas ───────────────────────────────────────────────
bool alertaSonido   = false;
bool alertaBPM      = false;
bool alertaTempAlta = false;
bool alertaTempBaja = false;

unsigned long tiempoAlertaSonido = 0;
const int DURACION_ALERTA_SONIDO = 3000;

// ── Buzzer sin delay ──────────────────────────────────────
unsigned long ultimoCambioBuzzer = 0;
bool estadoBuzzer   = false;
int intervaloBuzzer = 0;

// ── Funciones ─────────────────────────────────────────────
float leerTemperatura() {
  int lectura = analogRead(PIN_TMP36);
  // ESP32: ADC 12 bits (4096 pasos), referencia 3.3V
  float voltaje = (lectura * 3.3) / 4096.0;
  // TMP36: offset 0.5V, 10mV por °C
  return (voltaje - 0.5) * 100.0;
}

int leerBPMSimulado() {
  int valorPot = analogRead(PIN_POT);
  // ESP32: ADC 12 bits (4096 pasos)
  return map(valorPot, 0, 4095, 40, 180);
}

void actualizarBuzzer() {
  if (intervaloBuzzer == 0) {
    digitalWrite(PIN_BUZZER, LOW);
    estadoBuzzer = false;
    return;
  }
  if (millis() - ultimoCambioBuzzer >= intervaloBuzzer) {
    estadoBuzzer = !estadoBuzzer;
    digitalWrite(PIN_BUZZER, estadoBuzzer ? HIGH : LOW);
    ultimoCambioBuzzer = millis();
  }
}

// ── Setup ─────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

  pinMode(PIN_MIC, INPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  digitalWrite(PIN_BUZZER, LOW);

  // ESP32: configurar resolución ADC a 12 bits
  analogReadResolution(12);
  // Atenuar señal para rango completo 0-3.3V
  analogSetAttenuation(ADC_11db);

  Serial.println("Sistema iniciado en ESP32.");
  Serial.print("Rango seguro temp: ");
  Serial.print(TEMP_MIN_BEBE); Serial.print("C - ");
  Serial.print(TEMP_MAX_BEBE); Serial.println("C");
  Serial.println("BPM alto: >100");
}

// ── Loop ──────────────────────────────────────────────────
void loop() {

  // ── BPM simulado con potenciómetro ────────────────────
  bpmSimulado = leerBPMSimulado();
  alertaBPM   = (bpmSimulado >= BPM_ALTO);

  // ── Temperatura cada 2s ───────────────────────────────
  if (millis() - ultimaLecturaTemp > INTERVALO_TEMP) {
    temperatura    = leerTemperatura();
    alertaTempAlta = (temperatura > TEMP_MAX_BEBE);
    alertaTempBaja = (temperatura < TEMP_MIN_BEBE);

    if (alertaTempAlta) {
      Serial.print("ALERTA: Temp alta ");
      Serial.print(temperatura, 1); Serial.println("C");
    } else if (alertaTempBaja) {
      Serial.print("ALERTA: Temp baja ");
      Serial.print(temperatura, 1); Serial.println("C");
    }

    ultimaLecturaTemp = millis();
  }

  // ── Detección de llanto ───────────────────────────────
  int sonido = digitalRead(PIN_MIC);
  if (sonido == LOW && !alertaSonido) {
    alertaSonido = true;
    tiempoAlertaSonido = millis();
    Serial.println("ALERTA: Llanto detectado.");
  }
  if (alertaSonido && millis() - tiempoAlertaSonido > DURACION_ALERTA_SONIDO) {
    alertaSonido = false;
  }

  // ── Control del buzzer por prioridad ──────────────────
  if (alertaSonido && alertaBPM) {
    intervaloBuzzer = 80;    // Llanto + BPM alto: muy urgente
  } else if (alertaBPM) {
    intervaloBuzzer = 150;   // Solo BPM alto: rápido
  } else if (alertaSonido) {
    intervaloBuzzer = 400;   // Solo llanto: pausado
  } else if (alertaTempAlta) {
    intervaloBuzzer = 1000;  // Temp alta: lento
  } else if (alertaTempBaja) {
    intervaloBuzzer = 600;   // Temp baja: medio
  } else {
    intervaloBuzzer = 0;     // Sin alerta: silencio
  }

  actualizarBuzzer();

  // ── Serial Monitor ────────────────────────────────────
  Serial.print("BPM="); Serial.print(bpmSimulado);
  Serial.print(bpmSimulado >= BPM_ALTO ? " [ALTO]" : " [normal]");
  Serial.print(" | Temp="); Serial.print(temperatura, 1); Serial.print("C");

  if      (alertaTempAlta) Serial.print(" [TEMP ALTA]");
  else if (alertaTempBaja) Serial.print(" [TEMP BAJA]");
  else                     Serial.print(" [TEMP OK]");

  Serial.print(" | Sonido=");
  Serial.print(sonido == LOW ? "LLANTO" : "normal");
  Serial.println();

  delay(100); // Pequeña pausa para no saturar el Serial
}
