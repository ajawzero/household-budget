import { type ButtonHTMLAttributes, forwardRef } from "react";
import { cn } from "../../lib/cn";

type Variant = "primary" | "secondary" | "ghost";
type Size = "md" | "sm" | "icon";

const VARIANTS: Record<Variant, string> = {
  // Ink, never orange. Orange means spending in this app.
  primary: "bg-primary text-on-primary hover:bg-primary-hover",
  secondary: "bg-card text-fg border border-line hover:bg-hover",
  ghost: "bg-transparent text-fg hover:bg-active",
};

// 44px on phones (touch), 36px from the tablet size up.
const SIZES: Record<Size, string> = {
  md: "h-11 md:h-9 px-4 text-sm",
  sm: "h-11 md:h-8 px-3 text-sm",
  icon: "h-11 w-11 md:h-9 md:w-9 justify-center",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", className, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        // A slight press on tap and click. Never on a disabled button.
        "inline-flex items-center gap-2 rounded-lg font-medium transition duration-100 enabled:active:scale-98",
        "disabled:cursor-default disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    />
  );
});
