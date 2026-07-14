<script setup lang="ts">
import { computed, ref } from 'vue'
import { APPLY } from '../content'

const contact = ref('')
const remark = ref('')
const agreed = ref(false)
/** 蜜罐: 真人看不见也 tab 不到, 脚本会一股脑填满。填了就当机器人静默丢弃。 */
const website = ref('')

const state = ref<'idle' | 'sending' | 'ok' | 'err'>('idle')
const errMsg = ref('')

const canSubmit = computed(
  () => contact.value.trim().length >= 2 && agreed.value && state.value !== 'sending',
)

async function submit() {
  if (!canSubmit.value) return
  state.value = 'sending'
  errMsg.value = ''

  try {
    const res = await fetch('/api/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        contact: contact.value.trim(),
        remark: remark.value.trim(),
        website: website.value, // 蜜罐, 后端据此静默丢弃
      }),
    })

    if (res.ok) {
      state.value = 'ok'
      return
    }

    // 429 = 同 IP 提交太频繁; 其余按后端 detail 显示
    const data = await res.json().catch(() => null)
    errMsg.value = data?.detail || (res.status === 429 ? '提交太频繁了，过会儿再试。' : '提交失败，稍后再试。')
    state.value = 'err'
  } catch {
    errMsg.value = '网络没通，检查一下再试。'
    state.value = 'err'
  }
}
</script>

<template>
  <section class="sec apply" id="apply">
    <div class="wrap inner">
      <div class="left">
        <p class="sec-tag reveal">05 / 内测</p>
        <h2 class="sec-title reveal" :style="{ '--d': '60ms' }">{{ APPLY.title }}</h2>
        <p class="sec-lede reveal" :style="{ '--d': '120ms' }">{{ APPLY.lede }}</p>
      </div>

      <div class="right reveal" :style="{ '--d': '180ms' }">
        <!-- 提交成功后整块换成回执, 不留一个还能再点的按钮 -->
        <div v-if="state === 'ok'" class="done">
          <span class="done-mark num" aria-hidden="true">✓</span>
          <h3 class="done-t">{{ APPLY.okTitle }}</h3>
          <p class="done-b">{{ APPLY.okBody }}</p>
        </div>

        <form v-else class="form" novalidate @submit.prevent="submit">
          <label class="field">
            <span class="lab">{{ APPLY.fields.contactLabel }}</span>
            <input
              v-model="contact"
              type="text"
              autocomplete="off"
              :placeholder="APPLY.fields.contactPlaceholder"
              maxlength="60"
              required
            />
          </label>

          <label class="field">
            <span class="lab">{{ APPLY.fields.remarkLabel }}</span>
            <textarea
              v-model="remark"
              rows="4"
              maxlength="400"
              :placeholder="APPLY.fields.remarkPlaceholder"
            ></textarea>
          </label>

          <!-- 蜜罐: 对真人不可见、不可聚焦、读屏器跳过 -->
          <div class="hp" aria-hidden="true">
            <label>
              网址
              <input v-model="website" type="text" tabindex="-1" autocomplete="off" />
            </label>
          </div>

          <label class="agree">
            <input v-model="agreed" type="checkbox" />
            <span>{{ APPLY.agree }}</span>
          </label>

          <p v-if="state === 'err'" class="err" role="alert">{{ errMsg }}</p>

          <button type="submit" class="btn" :disabled="!canSubmit">
            {{ state === 'sending' ? APPLY.submitting : APPLY.submit }}
          </button>
        </form>
      </div>
    </div>
  </section>
</template>

<style scoped>
.apply {
  background: linear-gradient(180deg, rgba(8, 9, 12, 0.72), transparent 60%);
}

.inner {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 64px;
  align-items: start;
}

/* ---------- 表单 ---------- */
.form {
  background: var(--ink-2);
  border: 1px solid var(--line);
  padding: 32px;
  display: grid;
  gap: 22px;
}

.field {
  display: grid;
  gap: 9px;
}

.lab {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.1em;
  color: var(--fg-dim);
}

input[type='text'],
textarea {
  width: 100%;
  background: var(--ink-0);
  border: 1px solid var(--line);
  color: var(--fg-strong);
  font-family: inherit;
  font-size: 15px;
  line-height: 1.6;
  padding: 12px 14px;
  outline: none;
  transition:
    border-color 0.2s ease,
    box-shadow 0.2s ease;
  resize: vertical;
}

input[type='text']:focus,
textarea:focus {
  border-color: var(--amber);
  box-shadow: 0 0 0 3px var(--amber-glow);
}

::placeholder {
  color: var(--fg-faint);
}

/* 蜜罐: 挪出视口而不是 display:none —— 有些脚本会跳过 display:none 的字段 */
.hp {
  position: absolute;
  left: -9999px;
  width: 1px;
  height: 1px;
  overflow: hidden;
}

.agree {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 13px;
  color: var(--fg-dim);
  line-height: 1.6;
  cursor: pointer;
}

.agree input {
  margin-top: 4px;
  accent-color: var(--amber);
  flex: none;
}

.err {
  font-size: 13px;
  color: var(--up);
}

.btn {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 14px 20px;
  background: var(--amber);
  color: var(--ink-0);
  border: 0;
  cursor: pointer;
  transition:
    transform 0.18s ease,
    opacity 0.2s ease,
    box-shadow 0.25s ease;
}

.btn:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 10px 30px -12px rgba(240, 165, 0, 0.65);
}

.btn:disabled {
  opacity: 0.34;
  cursor: not-allowed;
}

/* ---------- 回执 ---------- */
.done {
  background: var(--ink-2);
  border: 1px solid rgba(47, 169, 107, 0.4);
  padding: 44px 32px;
  text-align: center;
}

.done-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border: 1px solid var(--down);
  color: var(--down);
  font-size: 20px;
  margin-bottom: 18px;
}

.done-t {
  font-size: 22px;
  margin-bottom: 8px;
}

.done-b {
  font-size: 14.5px;
  color: var(--fg-dim);
}

@media (max-width: 860px) {
  .inner {
    grid-template-columns: 1fr;
    gap: 36px;
  }
  .form {
    padding: 24px 20px;
  }
  /* 手机上输入框字号提到 16px, 否则 iOS Safari 聚焦时会自动放大整页 */
  input[type='text'],
  textarea {
    font-size: 16px;
  }
}
</style>
