import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/primitives/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "@/components/primitives/dropdown-menu";
import { applyTheme, readThemePreference, resolveTheme, storeThemePreference, themeChangeEvent, watchSystemTheme, type ThemePreference } from "@/lib/theme";

const options: { value: ThemePreference; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];

export function ThemeToggle() {
  const [preference, setPreference] = useState<ThemePreference>(readThemePreference);
  useEffect(() => (preference === "system" ? watchSystemTheme(() => applyTheme("system")) : undefined), [preference]);
  // Stay in sync when the theme is changed from Settings or the command menu.
  useEffect(() => {
    const sync = () => setPreference(readThemePreference());
    window.addEventListener(themeChangeEvent, sync);
    return () => window.removeEventListener(themeChangeEvent, sync);
  }, []);
  const Icon = resolveTheme(preference) === "dark" ? Moon : Sun;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="ghost" size="icon" aria-label="Change theme" />}>
        <Icon aria-hidden="true" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-36">
        <DropdownMenuRadioGroup
          value={preference}
          onValueChange={(value) => { const next = value as ThemePreference; setPreference(next); storeThemePreference(next); }}
        >
          {options.map(({ value, label, icon: OptionIcon }) => (
            <DropdownMenuRadioItem key={value} value={value}><OptionIcon aria-hidden="true" />{label}</DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
