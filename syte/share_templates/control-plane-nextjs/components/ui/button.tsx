import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

const buttonVariants = cva("inline-flex h-10 items-center justify-center gap-2 rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/30 disabled:pointer-events-none disabled:opacity-50", {
  variants: {
    variant: {
      default: "bg-white text-black hover:bg-zinc-200",
      secondary: "bg-zinc-800 text-zinc-100 hover:bg-zinc-700",
      outline: "border border-zinc-700 bg-transparent text-zinc-100 hover:bg-zinc-900",
      destructive: "border border-red-900/70 bg-red-950/50 text-red-200 hover:bg-red-900/50",
      ghost: "text-zinc-300 hover:bg-zinc-900 hover:text-white",
    },
    size: { default: "px-4", sm: "h-8 px-3 text-xs", lg: "h-11 px-5", icon: "h-10 w-10" },
  },
  defaultVariants: { variant: "default", size: "default" },
});

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> { asChild?: boolean }
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild = false, ...props }, ref) => {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size }), className)} ref={ref} {...props} />;
});
Button.displayName = "Button";
