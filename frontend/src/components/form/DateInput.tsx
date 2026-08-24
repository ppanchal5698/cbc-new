import { forwardRef } from "react";

type Props = Omit<React.InputHTMLAttributes<HTMLInputElement>, "type" | "value" | "onChange"> & {
  value: string;
  onValueChange: (value: string) => void;
};

/**
 * Controlled date input. Native `type="date"` does not always fire `change`
 * (paste, autofill, programmatic fill), so state is synced on input and blur too.
 */
export const DateInput = forwardRef<HTMLInputElement, Props>(function DateInput(
  { value, onValueChange, onBlur, ...rest },
  ref,
) {
  const sync = (e: React.SyntheticEvent<HTMLInputElement>) => onValueChange(e.currentTarget.value);

  return (
    <input
      ref={ref}
      type="date"
      value={value}
      onChange={sync}
      onInput={sync}
      onBlur={(e) => {
        sync(e);
        onBlur?.(e);
      }}
      {...rest}
    />
  );
});
