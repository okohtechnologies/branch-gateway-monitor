import { useId, type ReactNode } from "react";

/**
 * A labelled form field. The render prop receives the id and the ARIA attributes the input needs, so the
 * label, the helper text and the error message are all programmatically tied to it.
 */
export function Field({
  label,
  hint,
  error,
  optional,
  children,
}: {
  label: string;
  hint?: ReactNode;
  error?: string;
  optional?: boolean;
  children: (p: { id: string; "aria-invalid": boolean; "aria-describedby": string | undefined }) => ReactNode;
}) {
  const id = useId();
  const descId = `${id}-d`;
  return (
    <div>
      <label htmlFor={id} className="label">
        {label} {optional && <span className="font-normal text-muted">(optional)</span>}
      </label>
      {children({ id, "aria-invalid": Boolean(error), "aria-describedby": error || hint ? descId : undefined })}
      {error ? (
        <p id={descId} className="field-error">
          {error}
        </p>
      ) : hint ? (
        <p id={descId} className="hint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
