import { useMutation } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { messageFor } from "@/lib/api-error";
import { PERSONA_OPTIONS, type Persona } from "@/lib/persona";
import * as authService from "@/services/auth";

interface RegisterFormProps {
  onRegistered: (email: string) => void;
}

export function RegisterForm({ onRegistered }: RegisterFormProps) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [persona, setPersona] = useState<Persona | null>(null);

  const registerMutation = useMutation({
    mutationFn: authService.register,
    onSuccess: () => onRegistered(email),
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!persona) return;
    registerMutation.mutate({ email, password, full_name: fullName || null, persona });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5" noValidate>
      <div className="flex flex-col gap-2">
        <Label htmlFor="register-name">Full name (optional)</Label>
        <Input
          id="register-name"
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
          placeholder="Jane Researcher"
        />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="register-email">Email</Label>
        <Input
          id="register-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@example.com"
        />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="register-password">Password</Label>
        <Input
          id="register-password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="At least 8 characters"
        />
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-sm font-medium">I am a…</legend>
        {PERSONA_OPTIONS.map((option) => (
          <label
            key={option.value}
            className="flex cursor-pointer items-start gap-3 rounded-md border border-input p-3 text-sm has-[:checked]:border-primary"
          >
            <input
              type="radio"
              name="persona"
              value={option.value}
              checked={persona === option.value}
              onChange={() => setPersona(option.value)}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">{option.label}</span>
              <span className="block text-xs text-muted-foreground">{option.description}</span>
            </span>
          </label>
        ))}
      </fieldset>

      {registerMutation.isError && (
        <p role="alert" className="text-sm text-destructive">
          {messageFor(registerMutation.error)}
        </p>
      )}

      <Button type="submit" disabled={registerMutation.isPending || !persona} className="mt-1">
        {registerMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
        {registerMutation.isPending ? "Creating account…" : "Create account"}
      </Button>
    </form>
  );
}
