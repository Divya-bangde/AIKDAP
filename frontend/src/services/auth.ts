import { request } from "@/services/client";
import type { components } from "@/types/api";

type UserCreate = components["schemas"]["UserCreate"];
type UserRead = components["schemas"]["UserRead"];
type UserUpdate = components["schemas"]["UserUpdate"];
type LoginRequest = components["schemas"]["LoginRequest"];
type TokenPair = components["schemas"]["TokenPair"];

export function register(payload: UserCreate) {
  return request<UserRead>("/api/v1/auth/register", {
    method: "POST",
    body: payload,
    auth: false,
  });
}

export function login(payload: LoginRequest) {
  return request<TokenPair>("/api/v1/auth/login", {
    method: "POST",
    body: payload,
    auth: false,
  });
}

export function currentUser() {
  return request<UserRead>("/api/v1/auth/me");
}

export function updateMe(payload: UserUpdate) {
  return request<UserRead>("/api/v1/auth/me", { method: "PATCH", body: payload });
}
