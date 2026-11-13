/** The maker's mark. Kept in one place so the wording stays consistent. */
export function Credit({ className = "" }: { className?: string }) {
  return <p className={`text-xs text-muted ${className}`}>Made by Okoh Technologies</p>;
}
