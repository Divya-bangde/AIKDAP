import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RegisterForm } from "@/features/auth/RegisterForm";
import * as authService from "@/services/auth";
import { makeUser } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

vi.mock("@/services/auth");

describe("RegisterForm — persona", () => {
  afterEach(() => vi.restoreAllMocks());

  it("requires a persona and sends it with the registration", async () => {
    vi.mocked(authService.register).mockResolvedValue(makeUser({ persona: "student" }));
    const onRegistered = vi.fn();
    const user = userEvent.setup();

    renderWithProviders(<RegisterForm onRegistered={onRegistered} />);
    await user.type(screen.getByLabelText("Email"), "s@example.com");
    await user.type(screen.getByLabelText("Password"), "password123");

    const submit = screen.getByRole("button", { name: "Create account" });
    expect(submit).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: /student/i }));
    await user.click(submit);

    await waitFor(() => expect(onRegistered).toHaveBeenCalledWith("s@example.com"));
    expect(vi.mocked(authService.register).mock.calls[0][0]).toMatchObject({
      email: "s@example.com",
      persona: "student",
    });
  });
});
