import { useClusterStore } from "../store/clusterStore";

export default function ToastStack() {
  const toasts = useClusterStore((state) => state.toasts);
  const removeToast = useClusterStore((state) => state.removeToast);

  if (toasts.length === 0) {
    return null;
  }

  return (
    <div className="toast-stack">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast--${toast.tone}`}>
          <span>{toast.message}</span>
          <button
            className="toast__close"
            type="button"
            aria-label="Dismiss notification"
            onClick={() => removeToast(toast.id)}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
