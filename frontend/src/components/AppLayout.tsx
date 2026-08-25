import { Layout, Menu, theme } from 'antd'
import { FileOutlined, HistoryOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useNavigate, useLocation, Outlet } from 'react-router-dom'

const { Sider, Content, Header } = Layout

const menuItems = [
  { key: '/', icon: <FileOutlined />, label: '脚本库' },
  { key: '/history', icon: <HistoryOutlined />, label: '运行历史' },
]

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const { token: { colorBgContainer, borderRadiusLG } } = theme.useToken()

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
      <Sider
        collapsible
        theme="dark"
        style={{
          overflow: 'auto',
          height: '100vh',
          position: 'fixed',
          left: 0,
          top: 0,
          bottom: 0,
        }}
      >
        <div style={{
          height: 64,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
          color: '#fff',
          fontSize: 20,
          fontWeight: 'bold',
          borderBottom: '1px solid rgba(255,255,255,0.1)',
        }}>
          <ThunderboltOutlined style={{ fontSize: 24, color: '#1677ff' }} />
          ScriptHub
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderRight: 0 }}
        />
      </Sider>
      <Layout style={{ marginLeft: 200, height: '100vh', overflow: 'hidden' }}>
        <Header style={{
          background: colorBgContainer,
          padding: '0 24px',
          fontSize: 16,
          fontWeight: 500,
          borderBottom: '1px solid #f0f0f0',
          display: 'flex',
          alignItems: 'center',
          flexShrink: 0,
        }}>
          {location.pathname === '/' ? '脚本库' : location.pathname === '/history' ? '运行历史' : '脚本详情'}
        </Header>
        <Content style={{
          margin: 24,
          padding: 24,
          background: colorBgContainer,
          borderRadius: borderRadiusLG,
          overflow: 'hidden',
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
        }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
