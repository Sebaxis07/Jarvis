import cv2
import time
import numpy as np

def simple_scene_summary(frame):
    # Resumen básico: brillo promedio + si hay “mucho movimiento” (muy simple)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(np.mean(gray))
    return f"Brillo promedio: {brightness:.1f} (0 oscuro - 255 brillante)."

def main():
    cap = cv2.VideoCapture(0)  # 0 = cámara principal
    if not cap.isOpened():
        print("No pude abrir la cámara. Prueba con 1,2... en VideoCapture(x).")
        return

    print("Cámara lista. Teclas: [v]=ver/analizar, [q]=salir")

    while True:
        ok, frame = cap.read()
        if not ok:
            print("No pude leer frame.")
            break

        cv2.imshow("Jarvis Eyes", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break

        if key == ord('v'):
            summary = simple_scene_summary(frame)
            print("OJOS:", summary)

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
